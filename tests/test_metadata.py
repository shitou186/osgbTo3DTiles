"""单元测试：元数据解析与坐标变换。"""

import math
import os
import tempfile

import numpy as np

from osgb2tiles.metadata import local_to_ecef_transform, _compute_convergence_angle, OsgeMetadata, parse_metadata


class TestParseMetadata:
    def test_origin_format(self):
        """测试 <SRS> + <Origin X="" Y="" Z=""/> 格式。"""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<ModelMetadata version="1">
  <SRS>EPSG:4547</SRS>
  <Origin X="500000.0" Y="3000000.0" Z="50.0"/>
</ModelMetadata>"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write(xml_content)
            f.flush()
            path = f.name

        try:
            meta = parse_metadata(path)
            assert meta.srs == "EPSG:4547"
            # origin_height = 原始值 + 大地水准面纠正
            assert abs(meta.origin_height - (50.0 + meta.geoid_offset)) < 1e-6
            assert isinstance(meta.origin_lon, float)
            assert isinstance(meta.origin_lat, float)
            # CGCS2000 3度带 117E，原点应在 117°E 附近
            assert 110 < meta.origin_lon < 125
            assert 20 < meta.origin_lat < 50
        finally:
            os.unlink(path)

    def test_srs_origin_format(self):
        """测试 <CoordSys> + <SRSOrigin>x y z</SRSOrigin> 格式。"""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<metadata>
  <CoordSys>EPSG:4547</CoordSys>
  <SRSOrigin>500000.0 3000000.0 0.0</SRSOrigin>
</metadata>"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write(xml_content)
            f.flush()
            path = f.name

        try:
            meta = parse_metadata(path)
            assert meta.srs == "EPSG:4547"
            assert 110 < meta.origin_lon < 125
        finally:
            os.unlink(path)

    def test_srs_origin_comma_separated(self):
        """测试 <SRSOrigin>x,y,z</SRSOrigin> 逗号分隔格式。"""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<metadata>
  <SRS>EPSG:4326</SRS>
  <SRSOrigin>121.30008672715195,24.99351645741518,0</SRSOrigin>
</metadata>"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write(xml_content)
            f.flush()
            path = f.name

        try:
            meta = parse_metadata(path)
            assert meta.srs == "EPSG:4326"
            assert abs(meta.origin_lon - 121.300087) < 1e-5
            assert abs(meta.origin_lat - 24.993516) < 1e-5
            # origin_height = 原始值 + 大地水准面纠正
            assert abs(meta.origin_height - (0.0 + meta.geoid_offset)) < 1e-6
        finally:
            os.unlink(path)


class TestLocalToEcefTransform:
    """测试 ENU → ECEF 坐标变换矩阵。"""

    def _make_metadata(self, lon=116.0, lat=39.0, height=0.0):
        return OsgeMetadata(
            origin_lon=lon, origin_lat=lat, origin_height=height,
            srs="EPSG:4326", bounding_box=(0, 0, 0, 0, 0, 0),
        )

    def test_z_up_to_ecef_axis_mapping(self):
        """验证 ENU Z-up → ECEF 轴向映射：ENU Up(0,0,1) → ECEF 径向向外。"""
        meta = self._make_metadata()
        m = local_to_ecef_transform(meta)

        R = m[:3, :3]

        # ENU Z轴(0,0,1) 即"朝上" → ECEF 径向向外
        enu_up = np.array([0, 0, 1], dtype=np.float64)
        ecef_up = R @ enu_up

        lon_rad = math.radians(meta.origin_lon)
        lat_rad = math.radians(meta.origin_lat)
        expected_up = np.array([
            math.cos(lat_rad) * math.cos(lon_rad),
            math.cos(lat_rad) * math.sin(lon_rad),
            math.sin(lat_rad),
        ])
        assert np.allclose(ecef_up, expected_up, atol=1e-10), (
            f"Z轴(上)映射错误: {ecef_up} != {expected_up}"
        )

    def test_translation_is_ecef_origin(self):
        """平移分量应为原点的 ECEF 坐标。"""
        from pyproj import Transformer
        meta = self._make_metadata(lon=121.0, lat=25.0, height=100.0)
        m = local_to_ecef_transform(meta)

        t = Transformer.from_crs("EPSG:4326", "EPSG:4978", always_xy=True)
        ecef_x, ecef_y, ecef_z = t.transform(121.0, 25.0, 100.0)

        assert abs(m[0, 3] - ecef_x) < 1e-6
        assert abs(m[1, 3] - ecef_y) < 1e-6
        assert abs(m[2, 3] - ecef_z) < 1e-6

    def test_column_major_output(self):
        """验证 column-major 展平后第 13-15 元素为平移分量。"""
        meta = self._make_metadata()
        m = local_to_ecef_transform(meta)
        flat = m.T.flatten().tolist()

        assert abs(flat[12] - m[0, 3]) < 1e-15
        assert abs(flat[13] - m[1, 3]) < 1e-15
        assert abs(flat[14] - m[2, 3]) < 1e-15

    def test_matrix_is_orthogonal(self):
        """旋转部分应为正交矩阵（R^T R = I）。"""
        meta = self._make_metadata()
        m = local_to_ecef_transform(meta)
        R = m[:3, :3]
        product = R.T @ R
        assert np.allclose(product, np.eye(3), atol=1e-10), (
            f"R^T R != I, max_err={np.abs(product - np.eye(3)).max()}"
        )


class TestConvergenceAngle:
    """测试子午线收敛角校正。"""

    def test_convergence_nonzero_for_projected_crs(self):
        """投影坐标系离中央经线较远处应有非零收敛角。"""
        # EPSG:4547 中央经线 114°E，117°E 离中央 3°
        angle = _compute_convergence_angle("EPSG:4547", 117.0, 39.0)
        assert abs(angle) > 0.01, f"预期非零收敛角，实际 {math.degrees(angle):.4f}°"

    def test_convergence_zero_at_central_meridian(self):
        """投影坐标系中央经线处收敛角应为零。"""
        # EPSG:4547 中央经线 114°E
        angle = _compute_convergence_angle("EPSG:4547", 114.0, 39.0)
        assert abs(angle) < 1e-6, f"中央经线收敛角应为零，实际 {math.degrees(angle):.6f}°"

    def test_no_convergence_for_geographic_crs(self):
        """地理坐标系（如 EPSG:4326）无投影，收敛角为零。"""
        angle = _compute_convergence_angle("EPSG:4326", 117.0, 39.0)
        assert abs(angle) < 1e-10, f"地理坐标系收敛角应为零，实际 {math.degrees(angle):.10f}°"

    def test_convergence_correction_preserves_orthogonality(self):
        """收敛角修正后矩阵仍正交。"""
        # EPSG:4547 在 117°E 处有约 1.89° 收敛角
        meta = OsgeMetadata(
            origin_lon=117.0, origin_lat=39.0, origin_height=0.0,
            srs="EPSG:4547", bounding_box=(0, 0, 0, 0, 0, 0),
        )
        m = local_to_ecef_transform(meta)
        R = m[:3, :3]
        assert np.allclose(R.T @ R, np.eye(3), atol=1e-10), "修正后矩阵不正交"

    def test_convergence_correction_affects_rotation(self):
        """有收敛角时，修正矩阵与纯 ENU 矩阵不同。"""
        # 使用投影 CRS（有收敛角）
        meta_proj = OsgeMetadata(
            origin_lon=117.0, origin_lat=39.0, origin_height=0.0,
            srs="EPSG:4547", bounding_box=(0, 0, 0, 0, 0, 0),
        )
        # 使用地理 CRS（无收敛角）
        meta_geo = OsgeMetadata(
            origin_lon=117.0, origin_lat=39.0, origin_height=0.0,
            srs="EPSG:4326", bounding_box=(0, 0, 0, 0, 0, 0),
        )
        m_proj = local_to_ecef_transform(meta_proj)
        m_geo = local_to_ecef_transform(meta_geo)
        # 两者旋转部分应不同（投影 CRS 有收敛角修正）
        assert not np.allclose(m_proj[:3, :3], m_geo[:3, :3], atol=1e-6), (
            "投影 CRS 的修正矩阵应与地理 CRS 不同"
        )

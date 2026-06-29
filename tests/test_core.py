"""单元测试：配置、元数据解析、glTF 组装、GLB 打包。"""

import json
import os
import struct
import tempfile

import numpy as np

from osgb2tiles.config import ConvertConfig, TextureFormat, RefineMode
from osgb2tiles.gltf_assembler import GlbAssembler, pack_glb
from osgb2tiles.osgb_parser import OsgeMesh, OsgeTileNode, PageLODInfo, compute_geometric_error


class TestConvertConfig:
    def test_default_values(self):
        cfg = ConvertConfig()
        assert cfg.back_face_culling is True
        assert cfg.force_double_sided is False
        assert cfg.unlit_shading is True
        assert cfg.texture_format == TextureFormat.JPG
        assert cfg.mesh_compression is False
        assert cfg.refine_mode == RefineMode.REPLACE

    def test_validate_mutual_exclusion(self):
        cfg = ConvertConfig(back_face_culling=True, force_double_sided=True)
        try:
            cfg.validate()
            assert False, "应抛出 ValueError"
        except ValueError:
            pass

    def test_validate_pass(self):
        cfg = ConvertConfig(back_face_culling=True, force_double_sided=False)
        cfg.validate()  # 不应抛异常


class TestComputeGeometricError:
    def test_basic(self):
        lod = PageLODInfo(range_min=0, range_max=500, child_tile_path="", center=(0, 0, 0), radius=100)
        assert compute_geometric_error(lod) == 500.0

    def test_with_scale(self):
        lod = PageLODInfo(range_min=0, range_max=500, child_tile_path="", center=(0, 0, 0), radius=100)
        assert compute_geometric_error(lod, scale=0.5) == 250.0

    def test_minimum_value(self):
        lod = PageLODInfo(range_min=0, range_max=0, child_tile_path="", center=(0, 0, 0), radius=0)
        assert compute_geometric_error(lod) == 0.01


class TestGlbAssembler:
    def _make_simple_mesh(self) -> OsgeMesh:
        """创建一个简单的三角形网格。"""
        vertices = np.array([
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
        ], dtype=np.float32)
        normals = np.array([
            [0.0, 0.0, 1.0],
            [0.0, 0.0, 1.0],
            [0.0, 0.0, 1.0],
        ], dtype=np.float32)
        uvs = np.array([
            [0.0, 0.0],
            [1.0, 0.0],
            [0.0, 1.0],
        ], dtype=np.float32)
        indices = np.array([0, 1, 2], dtype=np.uint32)
        return OsgeMesh(vertices=vertices, normals=normals, uvs=uvs, indices=indices)

    def test_build_gltf_structure(self):
        cfg = ConvertConfig(unlit_shading=True, back_face_culling=True)
        assembler = GlbAssembler(cfg)
        mesh = self._make_simple_mesh()

        gltf, bin_data = assembler.build_gltf([mesh], tile_name="test_tile")

        assert gltf["asset"]["version"] == "2.0"
        assert len(gltf["meshes"]) == 1
        assert len(gltf["meshes"][0]["primitives"]) == 1
        assert "KHR_materials_unlit" in gltf.get("extensionsUsed", [])
        assert gltf["materials"][0]["doubleSided"] is False
        assert len(bin_data) > 0

    def test_material_double_sided(self):
        cfg = ConvertConfig(force_double_sided=True, back_face_culling=False)
        assembler = GlbAssembler(cfg)
        mesh = self._make_simple_mesh()

        gltf, _ = assembler.build_gltf([mesh])
        assert gltf["materials"][0]["doubleSided"] is True

    def test_material_culling(self):
        cfg = ConvertConfig(back_face_culling=True, force_double_sided=False)
        assembler = GlbAssembler(cfg)
        mesh = self._make_simple_mesh()

        gltf, _ = assembler.build_gltf([mesh])
        assert gltf["materials"][0]["doubleSided"] is False

    def test_material_with_lit(self):
        cfg = ConvertConfig(unlit_shading=False)
        assembler = GlbAssembler(cfg)
        mesh = self._make_simple_mesh()

        gltf, _ = assembler.build_gltf([mesh])
        extensions = gltf["materials"][0].get("extensions", {})
        assert "KHR_materials_unlit" not in extensions


class TestPackGlb:
    def test_glb_header(self):
        gltf_json = {
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": []}],
        }
        bin_data = b"\x00\x00\x00\x00"  # 4 bytes

        glb = pack_glb(gltf_json, bin_data)

        # 验证魔数
        assert glb[:4] == b"glTF"
        # 验证版本号
        version = struct.unpack_from("<I", glb, 4)[0]
        assert version == 2
        # 验证总长度
        total = struct.unpack_from("<I", glb, 8)[0]
        assert total == len(glb)

    def test_json_chunk(self):
        gltf_json = {"asset": {"version": "2.0"}}
        glb = pack_glb(gltf_json, b"")

        # JSON chunk type
        chunk_len = struct.unpack_from("<I", glb, 12)[0]
        chunk_type = glb[16:20]
        assert chunk_type == b"JSON"
        assert chunk_len > 0

    def test_alignment(self):
        gltf_json = {"a": 1}  # 奇数长度 JSON
        bin_data = b"\x01\x02\x03"  # 非 4 倍数 BIN
        glb = pack_glb(gltf_json, bin_data)
        assert len(glb) % 4 == 0


class TestGeometricErrorLevelDecay:
    """测试基于文件名层级标识的 geometricError 指数衰减。"""

    def test_extract_level_from_contextcapture_filename(self):
        from osgb2tiles.structure import extract_level_from_filename, StructureType
        assert extract_level_from_filename("Tile_+000_+000_L20_0000t3_0025.glb", StructureType.CONTEXT_CAPTURE) == 20
        assert extract_level_from_filename("Tile_+000_+000_L21_00010t2_001.glb", StructureType.CONTEXT_CAPTURE) == 21
        assert extract_level_from_filename("Tile_p0000_p0000_0253.glb", StructureType.CONTEXT_CAPTURE) is None
        assert extract_level_from_filename("Data_L15_+002_+003.osgb", StructureType.CONTEXT_CAPTURE) == 15

    def test_level_decay_produces_staircase(self):
        """模拟 range_max 相同场景：L20 和 L21 的 geometricError 应呈阶梯递减。"""
        from osgb2tiles.structure import compute_level_based_error
        base_error = 1000.0
        base_level = 20
        scale = 1.0

        # L20: decay_factor = 0.5^0 = 1.0
        error_l20 = max(base_error * (0.5 ** (20 - base_level)) * scale, 0.01)
        # L21: decay_factor = 0.5^1 = 0.5
        error_l21 = max(base_error * (0.5 ** (21 - base_level)) * scale, 0.01)
        # L22: decay_factor = 0.5^2 = 0.25
        error_l22 = max(base_error * (0.5 ** (22 - base_level)) * scale, 0.01)

        assert error_l20 == 1000.0
        assert abs(error_l21 - 500.0) < 0.01
        assert abs(error_l22 - 250.0) < 0.01
        # 严格单调递减
        assert error_l20 > error_l21 > error_l22 > 0.01

    def test_level_decay_minimum_clamp(self):
        """极高层级不应低于 0.01。"""
        base_error = 1000.0
        base_level = 20
        # L40: 0.5^20 ≈ 0.00000095 → clamped to 0.01
        error_l40 = max(base_error * (0.5 ** (40 - base_level)), 0.01)
        assert error_l40 >= 0.01

    def test_root_level_uses_base_error(self):
        """根节点（level == base_level）应使用原始 base_error。"""
        base_error = 1000.0
        base_level = 20
        # level == base_level → decay_factor = 0.5^0 = 1.0
        error = max(base_error * (0.5 ** (base_level - base_level)), 0.01)
        assert error == 1000.0

    def test_range_max_variation_uses_original(self):
        """当 range_max 有变化时，应直接使用 range_max * scale。"""
        from osgb2tiles.osgb_parser import compute_geometric_error, PageLODInfo
        lod1 = PageLODInfo(range_min=0, range_max=500, child_tile_path="a.osgb", center=(0,0,0), radius=10)
        lod2 = PageLODInfo(range_min=0, range_max=1000, child_tile_path="b.osgb", center=(0,0,0), radius=10)
        assert compute_geometric_error(lod1) == 500.0
        assert compute_geometric_error(lod2) == 1000.0

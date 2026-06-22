"""单元测试：元数据解析。"""

import os
import tempfile

from osgb2tiles.metadata import parse_metadata


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
            assert meta.origin_height == 50.0
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
            assert meta.origin_height == 0.0
        finally:
            os.unlink(path)

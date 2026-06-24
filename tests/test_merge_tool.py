"""多工程合并工具单元测试。"""

import json
import os
import tempfile

from osgb2tiles.merge_tool import merge_tilesets, _merge_bounding_volumes, _bbox_to_minmax, _minmax_to_box


def _make_tileset(name: str, cx: float, cy: float, cz: float = 0, error: float = 100) -> dict:
    """创建测试用 tileset.json 内容。"""
    return {
        "asset": {"version": "1.1"},
        "geometricError": error,
        "root": {
            "boundingVolume": {"box": [cx, cy, cz, 50, 0, 0, 0, 50, 0, 0, 0, 50]},
            "geometricError": error,
            "refine": "REPLACE",
            "content": {"uri": "tiles/test.glb"},
        },
    }


class TestMergeTilesets:
    """tileset.json 合并测试。"""

    def test_merge_two_projects(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建两个子工程
            proj_a = os.path.join(tmpdir, "proj_a")
            proj_b = os.path.join(tmpdir, "proj_b")
            os.makedirs(proj_a)
            os.makedirs(proj_b)

            with open(os.path.join(proj_a, "tileset.json"), "w") as f:
                json.dump(_make_tileset("a", 100, 30, error=100), f)
            with open(os.path.join(proj_b, "tileset.json"), "w") as f:
                json.dump(_make_tileset("b", 200, 60, error=200), f)

            output_file = os.path.join(tmpdir, "merged", "tileset.json")
            merge_tilesets([proj_a, proj_b], output_file)

            assert os.path.exists(output_file)
            with open(output_file) as f:
                result = json.load(f)

            assert result["asset"]["version"] == "1.1"
            assert len(result["root"]["children"]) == 2
            assert result["geometricError"] == 400  # max(100, 200) * 2

    def test_merge_preserves_transform(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proj = os.path.join(tmpdir, "proj")
            os.makedirs(proj)

            ts = _make_tileset("a", 100, 30)
            ts["root"]["transform"] = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 100, 30, 0, 1]
            with open(os.path.join(proj, "tileset.json"), "w") as f:
                json.dump(ts, f)

            output_file = os.path.join(tmpdir, "merged", "tileset.json")
            merge_tilesets([proj], output_file)

            with open(output_file) as f:
                result = json.load(f)
            assert "transform" in result["root"]["children"][0]

    def test_skip_missing_tileset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proj_a = os.path.join(tmpdir, "proj_a")
            proj_b = os.path.join(tmpdir, "proj_b_no_tileset")
            os.makedirs(proj_a)
            os.makedirs(proj_b)

            with open(os.path.join(proj_a, "tileset.json"), "w") as f:
                json.dump(_make_tileset("a", 100, 30), f)

            output_file = os.path.join(tmpdir, "merged", "tileset.json")
            merge_tilesets([proj_a, proj_b], output_file)

            with open(output_file) as f:
                result = json.load(f)
            assert len(result["root"]["children"]) == 1


class TestBboxMerge:
    """包围盒合并测试。"""

    def test_merge_two_boxes(self):
        bv1 = {"box": [100, 30, 0, 10, 0, 0, 0, 10, 0, 0, 0, 10]}
        bv2 = {"box": [200, 60, 0, 10, 0, 0, 0, 10, 0, 0, 0, 10]}
        merged = _merge_bounding_volumes(bv1, bv2)
        b = merged["box"]
        # cx = (90+210)/2 = 150
        assert b[0] == 150
        # cy = (20+70)/2 = 45
        assert b[1] == 45
        # hx = (210-90)/2 = 60
        assert b[3] == 60

    def test_merge_box_and_sphere(self):
        bv1 = {"box": [100, 30, 0, 10, 0, 0, 0, 10, 0, 0, 0, 10]}
        bv2 = {"sphere": [200, 60, 0, 50]}
        merged = _merge_bounding_volumes(bv1, bv2)
        assert "box" in merged

    def test_merge_with_none(self):
        bv = {"box": [100, 30, 0, 10, 0, 0, 0, 10, 0, 0, 0, 10]}
        merged = _merge_bounding_volumes(None, bv)
        assert merged == bv

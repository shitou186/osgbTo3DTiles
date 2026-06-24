"""空间四叉树与阶段一重构器单元测试。"""

import numpy as np

from osgb2tiles.osgb_parser import OsgeMesh
from osgb2tiles.spatial_quadtree import (
    TileRecord,
    QuadTreeNode,
    build_quadtree,
    collect_leaf_nodes,
    collect_non_leaf_nodes,
    compute_merged_bbox,
    bbox_center_lonlat,
    _bbox_to_minmax,
    _minmax_to_box,
)


def _make_tile(name: str, cx: float, cy: float, cz: float = 0, hx: float = 10, hy: float = 10, hz: float = 10) -> TileRecord:
    """创建测试用瓦片记录。"""
    return TileRecord(
        name=name,
        bounding_volume={"box": [cx, cy, cz, hx, 0, 0, 0, hy, 0, 0, 0, hz]},
        geometric_error=100.0,
        meshes=[],
        content_uri=f"tiles/{name}.glb",
        center_lonlat=(cx, cy),
    )


class TestBuildQuadtree:
    """四叉树构建测试。"""

    def test_empty_tiles(self):
        root = build_quadtree([], max_per_leaf=4)
        assert root.tiles == []
        assert root.children is None

    def test_single_tile(self):
        tiles = [_make_tile("t1", 100, 30)]
        root = build_quadtree(tiles, max_per_leaf=4)
        assert root.children is None
        assert len(root.tiles) == 1

    def test_four_tiles_no_split(self):
        tiles = [_make_tile(f"t{i}", 100 + i, 30 + i) for i in range(4)]
        root = build_quadtree(tiles, max_per_leaf=4)
        assert root.children is None
        assert len(root.tiles) == 4

    def test_five_tiles_triggers_split(self):
        tiles = [_make_tile(f"t{i}", 100 + i * 10, 30 + i * 10) for i in range(5)]
        root = build_quadtree(tiles, max_per_leaf=4)
        assert root.children is not None
        assert len(root.children) == 4
        assert len(root.tiles) == 0

    def test_tiles_distributed_to_quadrants(self):
        # 4 个瓦片分别在 4 个象限
        tiles = [
            _make_tile("bl", 100, 30),    # 左下
            _make_tile("br", 200, 30),    # 右下
            _make_tile("tl", 100, 60),    # 左上
            _make_tile("tr", 200, 60),    # 右上
            _make_tile("extra", 150, 45), # 中间，触发分裂
        ]
        root = build_quadtree(tiles, max_per_leaf=4)
        assert root.children is not None

        # 收集所有叶子
        leaves = collect_leaf_nodes(root)
        total_tiles = sum(len(l.tiles) for l in leaves)
        assert total_tiles == 5

    def test_many_tiles(self):
        tiles = [_make_tile(f"t{i}", 100 + i * 0.1, 30 + i * 0.1) for i in range(100)]
        root = build_quadtree(tiles, max_per_leaf=4)
        leaves = collect_leaf_nodes(root)
        total_tiles = sum(len(l.tiles) for l in leaves)
        assert total_tiles == 100
        # 每个叶子不超过 4 个瓦片
        for leaf in leaves:
            assert len(leaf.tiles) <= 4


class TestCollectNodes:
    """节点收集测试。"""

    def test_collect_leaves_flat(self):
        tiles = [_make_tile(f"t{i}", 100 + i, 30) for i in range(3)]
        root = build_quadtree(tiles, max_per_leaf=4)
        leaves = collect_leaf_nodes(root)
        assert len(leaves) == 1
        assert leaves[0].tiles == tiles

    def test_collect_non_leaves(self):
        tiles = [_make_tile(f"t{i}", 100 + i * 10, 30 + i * 10) for i in range(5)]
        root = build_quadtree(tiles, max_per_leaf=4)
        non_leaves = collect_non_leaf_nodes(root)
        assert len(non_leaves) >= 1
        # 非叶子节点不应有 tiles
        for nl in non_leaves:
            assert len(nl.tiles) == 0


class TestBboxOperations:
    """包围盒操作测试。"""

    def test_bbox_center_lonlat_box(self):
        bv = {"box": [100, 30, 0, 10, 0, 0, 0, 10, 0, 0, 0, 10]}
        cx, cy = bbox_center_lonlat(bv)
        assert cx == 100
        assert cy == 30

    def test_bbox_center_lonlat_sphere(self):
        bv = {"sphere": [100, 30, 0, 50]}
        cx, cy = bbox_center_lonlat(bv)
        assert cx == 100
        assert cy == 30

    def test_bbox_to_minmax_box(self):
        bv = {"box": [100, 30, 0, 10, 0, 0, 0, 20, 0, 0, 0, 30]}
        mn, mx = _bbox_to_minmax(bv)
        assert mn == [90, 10, -30]
        assert mx == [110, 50, 30]

    def test_minmax_to_box(self):
        box = _minmax_to_box([90, 10, -30], [110, 50, 30])
        b = box["box"]
        assert b[0] == 100  # cx
        assert b[1] == 30   # cy
        assert b[2] == 0    # cz
        assert b[3] == 10   # hx
        assert b[7] == 20   # hy
        assert b[11] == 30  # hz

    def test_compute_merged_bbox(self):
        tiles = [
            _make_tile("t1", 100, 30, 0, 10, 10, 10),
            _make_tile("t2", 200, 60, 0, 10, 10, 10),
        ]
        merged = compute_merged_bbox(tiles)
        b = merged["box"]
        assert b[0] == 150  # cx = (100+200)/2
        assert b[1] == 45   # cy = (30+60)/2
        assert b[3] == 60   # hx = (210-90)/2

    def test_merge_bounding_volumes_box(self):
        from osgb2tiles.merge_tool import _merge_bounding_volumes
        bv1 = {"box": [100, 30, 0, 10, 0, 0, 0, 10, 0, 0, 0, 10]}
        bv2 = {"box": [200, 60, 0, 10, 0, 0, 0, 10, 0, 0, 0, 10]}
        merged = _merge_bounding_volumes(bv1, bv2)
        b = merged["box"]
        assert b[0] == 150  # cx
        assert b[3] == 60   # hx


class TestMergeMeshes:
    """网格合并测试。"""

    def test_merge_two_meshes(self):
        from osgb2tiles.top_level_reconstructor import merge_meshes

        mesh1 = OsgeMesh(
            vertices=np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32),
            normals=np.array([[0, 0, 1], [0, 0, 1], [0, 0, 1]], dtype=np.float32),
            uvs=np.array([[0, 0], [1, 0], [0, 1]], dtype=np.float32),
            indices=np.array([0, 1, 2], dtype=np.uint32),
        )
        mesh2 = OsgeMesh(
            vertices=np.array([[2, 0, 0], [3, 0, 0], [2, 1, 0]], dtype=np.float32),
            normals=np.array([[0, 0, 1], [0, 0, 1], [0, 0, 1]], dtype=np.float32),
            uvs=np.array([[0, 0], [1, 0], [0, 1]], dtype=np.float32),
            indices=np.array([0, 1, 2], dtype=np.uint32),
        )

        merged = merge_meshes([[mesh1], [mesh2]])
        assert merged is not None
        assert len(merged.vertices) == 6
        assert len(merged.indices) == 6
        # 第二个 mesh 的索引应该偏移了 3
        assert merged.indices[3] == 3
        assert merged.indices[4] == 4
        assert merged.indices[5] == 5

    def test_merge_empty_meshes(self):
        from osgb2tiles.top_level_reconstructor import merge_meshes
        result = merge_meshes([[]])
        assert result is None

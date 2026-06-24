"""空间四叉树模块。

基于 WGS84 经纬度构建四叉树空间索引，用于将大量零散的顶层瓦片
归类到有限的树节点中，解决 DJI Terra 等平铺结构导致的顶层 children 堆积问题。

坐标系选择：WGS84 (lon, lat) 作为四叉树的两个维度，高度归入 Z 范围。
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .osgb_parser import OsgeMesh


@dataclass
class TileRecord:
    """瓦片引用记录。"""
    name: str
    bounding_volume: dict
    geometric_error: float
    meshes: List[OsgeMesh]
    content_uri: str
    center_lonlat: Tuple[float, float]


@dataclass
class QuadTreeNode:
    """四叉树节点。"""
    bounds: Tuple[float, float, float, float]  # (lon_min, lat_min, lon_max, lat_max)
    children: Optional[List["QuadTreeNode"]] = None  # 4 个子节点，None 表示叶子
    tiles: List[TileRecord] = field(default_factory=list)
    merged_bbox: Optional[dict] = None
    geometric_error: float = 0.0


def bbox_center_lonlat(bounding_volume: dict) -> Tuple[float, float]:
    """从 3D Tiles boundingVolume 提取中心点经纬度。

    支持 box 和 sphere 两种格式。
    注意：boundingVolume 中的坐标是 ECEF 或局部坐标，
    此处仅提取 X/Y 分量作为近似 lon/lat 用于空间排序。
    实际 WGS84 转换在上层完成。
    """
    if "box" in bounding_volume:
        box = bounding_volume["box"]
        return (float(box[0]), float(box[1]))
    elif "sphere" in bounding_volume:
        s = bounding_volume["sphere"]
        return (float(s[0]), float(s[1]))
    return (0.0, 0.0)


def build_quadtree(
    tiles: List[TileRecord],
    max_per_leaf: int = 4,
) -> QuadTreeNode:
    """从瓦片列表构建四叉树。

    1. 计算所有瓦片的全局 lon/lat 包围范围
    2. 创建根节点
    3. 递归分裂：当叶子节点 tile 数 > max_per_leaf 时，按中位点四分

    Args:
        tiles: 瓦片记录列表
        max_per_leaf: 每个叶子节点最多容纳的瓦片数

    Returns:
        四叉树根节点
    """
    if not tiles:
        return QuadTreeNode(bounds=(0, 0, 0, 0))

    # 计算全局包围范围
    all_lons = [t.center_lonlat[0] for t in tiles]
    all_lats = [t.center_lonlat[1] for t in tiles]
    global_bounds = (
        min(all_lons),
        min(all_lats),
        max(all_lons),
        max(all_lats),
    )

    root = QuadTreeNode(bounds=global_bounds, tiles=tiles)
    _split_recursive(root, max_per_leaf)
    return root


def _split_recursive(node: QuadTreeNode, max_per_leaf: int):
    """递归分裂四叉树节点。"""
    if len(node.tiles) <= max_per_leaf:
        return

    lon_min, lat_min, lon_max, lat_max = node.bounds

    # 如果包围范围退化为点，无法分裂
    if lon_max - lon_min < 1e-10 and lat_max - lat_min < 1e-10:
        return

    # 按中位点分裂为 4 个象限
    lon_mid = (lon_min + lon_max) / 2.0
    lat_mid = (lat_min + lat_max) / 2.0

    quadrants = [
        (lon_min, lat_min, lon_mid, lat_mid),  # 左下
        (lon_mid, lat_min, lon_max, lat_mid),  # 右下
        (lon_min, lat_mid, lon_mid, lat_max),  # 左上
        (lon_mid, lat_mid, lon_max, lat_max),  # 右上
    ]

    children = []
    for q_bounds in quadrants:
        child = QuadTreeNode(bounds=q_bounds)
        children.append(child)

    # 将瓦片分配到对应象限
    for tile in node.tiles:
        lon, lat = tile.center_lonlat
        idx = 0
        if lon >= lon_mid:
            idx += 1
        if lat >= lat_mid:
            idx += 2
        children[idx].tiles.append(tile)

    node.tiles = []
    node.children = children

    # 递归分裂子节点
    for child in node.children:
        _split_recursive(child, max_per_leaf)


def collect_leaf_nodes(node: QuadTreeNode) -> List[QuadTreeNode]:
    """收集四叉树中所有叶子节点。"""
    if node.children is None:
        return [node]
    result = []
    for child in node.children:
        result.extend(collect_leaf_nodes(child))
    return result


def collect_non_leaf_nodes(node: QuadTreeNode) -> List[QuadTreeNode]:
    """收集四叉树中所有非叶子节点（从底到顶）。"""
    result = []
    if node.children is not None:
        for child in node.children:
            result.extend(collect_non_leaf_nodes(child))
        result.append(node)
    return result


def compute_merged_bbox(tiles: List[TileRecord]) -> dict:
    """从多个瓦片的包围盒计算合并后的最小外接包围盒。

    使用 box 格式：[cx, cy, cz, hx, 0, 0, 0, hy, 0, 0, 0, hz]
    """
    all_min = [float("inf"), float("inf"), float("inf")]
    all_max = [float("-inf"), float("-inf"), float("-inf")]

    for tile in tiles:
        t_min, t_max = _bbox_to_minmax(tile.bounding_volume)
        for i in range(3):
            all_min[i] = min(all_min[i], t_min[i])
            all_max[i] = max(all_max[i], t_max[i])

    return _minmax_to_box(all_min, all_max)


def _bbox_to_minmax(bbox: dict) -> Tuple[List[float], List[float]]:
    """将 3D Tiles boundingVolume 转为 (min_xyz, max_xyz)。"""
    if "box" in bbox:
        box = bbox["box"]
        cx, cy, cz = box[0], box[1], box[2]
        hx, hy, hz = box[3], box[7], box[11]
        return [cx - hx, cy - hy, cz - hz], [cx + hx, cy + hy, cz + hz]
    elif "sphere" in bbox:
        s = bbox["sphere"]
        cx, cy, cz, r = s[0], s[1], s[2], s[3]
        return [cx - r, cy - r, cz - r], [cx + r, cy + r, cz + r]
    return [0, 0, 0], [0, 0, 0]


def _minmax_to_box(min_xyz: list, max_xyz: list) -> dict:
    """将 (min_xyz, max_xyz) 转为 3D Tiles box 格式。"""
    cx = (min_xyz[0] + max_xyz[0]) / 2
    cy = (min_xyz[1] + max_xyz[1]) / 2
    cz = (min_xyz[2] + max_xyz[2]) / 2
    hx = (max_xyz[0] - min_xyz[0]) / 2
    hy = (max_xyz[1] - min_xyz[1]) / 2
    hz = (max_xyz[2] - min_xyz[2]) / 2
    return {
        "box": [cx, cy, cz, hx, 0.0, 0.0, 0.0, hy, 0.0, 0.0, 0.0, hz]
    }

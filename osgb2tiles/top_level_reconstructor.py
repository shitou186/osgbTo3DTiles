"""阶段一：顶层空间树重构器。

解决 DJI Terra 等平铺结构导致的 tileset.json 核心 children 堆积问题。
通过四叉树将零散瓦片归类，自底向上合并网格并大幅抽稀，
生成宏观大瓦片作为新的顶层节点。
"""

import os
import sys
from typing import List

import numpy as np

from .config import ConvertConfig
from .gltf_assembler import GlbAssembler, pack_glb
from .b3dm import package_to_b3dm
from .osgb_parser import OsgeMesh
from .spatial_quadtree import (
    QuadTreeNode,
    TileRecord,
    build_quadtree,
    collect_non_leaf_nodes,
    compute_merged_bbox,
)
from .texture import load_texture


def reconstruct_top_levels(
    tiles: List[TileRecord],
    config: ConvertConfig,
    assembler: GlbAssembler,
    output_dir: str,
    tile_counter_start: int = 0,
) -> dict:
    """阶段一主入口：重建顶层空间树。

    流程：
    1. 构建四叉树（叶子阈值=4）
    2. 自底向上遍历非叶子节点
    3. 对每个非叶子节点：合并子节点网格 → 大幅简化 → 输出宏观瓦片
    4. 返回重建后的根 tile dict

    Args:
        tiles: 所有顶层叶子瓦片记录
        config: 转换配置
        assembler: GLB 组装器
        output_dir: 输出目录
        tile_counter_start: 瓦片计数器起始值

    Returns:
        重建后的根 tile dict（children 数 = 四叉树叶子数）
    """
    if len(tiles) <= 4:
        # 瓦片数不多，无需重构
        return _build_simple_root(tiles, config)

    print(f"  [阶段一] 空间四叉树重构: {len(tiles)} 个瓦片")

    # 1. 构建四叉树
    quadtree = build_quadtree(tiles, max_per_leaf=4)

    # 2. 自底向上处理非叶子节点
    non_leaf_nodes = collect_non_leaf_nodes(quadtree)
    tile_counter = tile_counter_start

    for node in non_leaf_nodes:
        if node.children is None:
            continue

        # 收集子节点的所有瓦片
        all_child_tiles = []
        for child in node.children:
            all_child_tiles.extend(_collect_all_tiles(child))

        if not all_child_tiles:
            continue

        # 合并网格
        merged_mesh = merge_meshes([t.meshes for t in all_child_tiles])
        if merged_mesh is None or len(merged_mesh.indices) == 0:
            continue

        # 大幅抽稀到 10%
        simplified = _simplify_aggressively(merged_mesh, config)
        if simplified is None:
            continue

        # 条件 Draco：上层节点启用压缩
        use_draco = config.mesh_compression

        # 生成 GLB
        macro_mesh = OsgeMesh(
            vertices=simplified.vertices,
            normals=simplified.normals,
            uvs=simplified.uvs,
            indices=simplified.indices,
            texture_data=merged_mesh.texture_data,
            texture_path=merged_mesh.texture_path,
        )

        glb_bytes = _assemble_glb_safe(assembler, [macro_mesh], f"macro_{tile_counter}", use_draco)
        tile_bytes = _package_tile(glb_bytes, config)

        tile_counter += 1
        ext = ".b3dm" if config.tiles_version == "1.0" else ".glb"
        tile_name = f"quadtree_macro_{tile_counter:04d}{ext}"
        tile_rel_path = os.path.join("tiles", tile_name)

        full_path = os.path.join(output_dir, tile_rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(tile_bytes)

        # 计算几何误差
        child_errors = [t.geometric_error for t in all_child_tiles]
        node.geometric_error = max(child_errors) * 2.0

        # 合并包围盒
        node.merged_bbox = compute_merged_bbox(all_child_tiles)

        # 将子节点的瓦片引用转换为 tile dict
        child_tile_dicts = _build_child_tile_dicts(node.children, config)

        # 存储重构结果到节点
        node._reconstructed_tile = {
            "boundingVolume": node.merged_bbox,
            "geometricError": round(node.geometric_error, 2),
            "refine": config.refine_mode.value,
            "content": {"uri": tile_rel_path},
            "children": child_tile_dicts,
        }

        tri_count = len(simplified.indices) // 3
        draco_tag = "+Draco" if use_draco else ""
        print(f"    宏观瓦片 {tile_name}: {len(tile_bytes)/1024:.1f}KB, {tri_count}tris {draco_tag}")

    # 3. 组装根节点
    root = _assemble_quadtree_root(quadtree, config)
    print(f"  [阶段一] 完成: 根节点 children 数 = {len(root.get('children', []))}")
    return root


def merge_meshes(mesh_groups: List[List[OsgeMesh]]) -> OsgeMesh:
    """将多组网格合并为单一网格。

    - vertices/normals/uvs: np.concatenate
    - indices: 每组的 indices 加上顶点偏移
    - 纹理: 取第一个非空纹理
    """
    all_verts = []
    all_normals = []
    all_uvs = []
    all_indices = []
    vertex_offset = 0
    texture_data = None
    texture_path = None

    for mesh_list in mesh_groups:
        for mesh in mesh_list:
            if len(mesh.vertices) == 0:
                continue

            all_verts.append(mesh.vertices)
            if len(mesh.normals) > 0:
                all_normals.append(mesh.normals)
            if len(mesh.uvs) > 0:
                all_uvs.append(mesh.uvs)

            if len(mesh.indices) > 0:
                all_indices.append(mesh.indices + vertex_offset)

            vertex_offset += len(mesh.vertices)

            if texture_data is None and mesh.texture_data is not None:
                texture_data = mesh.texture_data
                texture_path = mesh.texture_path

    if not all_verts:
        return None

    return OsgeMesh(
        vertices=np.concatenate(all_verts),
        normals=np.concatenate(all_normals) if all_normals else np.empty((0, 3), dtype=np.float32),
        uvs=np.concatenate(all_uvs) if all_uvs else np.empty((0, 2), dtype=np.float32),
        indices=np.concatenate(all_indices) if all_indices else np.array([], dtype=np.uint32),
        texture_data=texture_data,
        texture_path=texture_path,
    )


def _simplify_aggressively(mesh: OsgeMesh, config: ConvertConfig) -> OsgeMesh:
    """对网格进行大幅简化（10% 保留率）。"""
    try:
        from .mesh_simplifier import simplify_mesh
    except ImportError:
        return mesh

    if len(mesh.indices) == 0 or len(mesh.vertices) == 0:
        return mesh

    result = simplify_mesh(
        mesh.vertices, mesh.normals, mesh.uvs, mesh.indices,
        target_ratio=0.10,  # 仅保留 10% 三角形
        target_error=config.simplify_error * 5.0,  # 放宽误差容忍
        optimize=True,
    )

    return OsgeMesh(
        vertices=result.vertices,
        normals=result.normals,
        uvs=result.uvs,
        indices=result.indices,
        texture_data=mesh.texture_data,
        texture_path=mesh.texture_path,
    )


def _collect_all_tiles(node: QuadTreeNode) -> List[TileRecord]:
    """递归收集节点下所有瓦片。"""
    result = list(node.tiles)
    if node.children:
        for child in node.children:
            result.extend(_collect_all_tiles(child))
    return result


def _build_child_tile_dicts(children: List[QuadTreeNode], config: ConvertConfig) -> List[dict]:
    """将四叉树子节点转换为 3D Tiles tile dict 列表。"""
    result = []
    for child in children:
        if child.children is not None and hasattr(child, "_reconstructed_tile"):
            # 非叶子节点：使用重构后的 tile
            result.append(child._reconstructed_tile)
        elif child.tiles:
            # 叶子节点：直接引用原始瓦片
            for tile in child.tiles:
                tile_dict = {
                    "boundingVolume": tile.bounding_volume,
                    "geometricError": round(tile.geometric_error, 2),
                    "refine": config.refine_mode.value,
                    "content": {"uri": tile.content_uri},
                }
                result.append(tile_dict)
    return result


def _assemble_quadtree_root(quadtree: QuadTreeNode, config: ConvertConfig) -> dict:
    """组装四叉树根节点为 tile dict。"""
    children = _build_child_tile_dicts(
        quadtree.children if quadtree.children else [],
        config,
    )

    # 如果根节点本身有重构结果
    if hasattr(quadtree, "_reconstructed_tile"):
        return quadtree._reconstructed_tile

    # 计算根节点的包围盒和误差
    all_tiles = _collect_all_tiles(quadtree)
    if not all_tiles:
        return {}

    root_bbox = compute_merged_bbox(all_tiles)
    root_error = max(t.geometric_error for t in all_tiles) * 2.0

    return {
        "boundingVolume": root_bbox,
        "geometricError": round(root_error, 2),
        "refine": config.refine_mode.value,
        "children": children,
    }


def _build_simple_root(tiles: List[TileRecord], config: ConvertConfig) -> dict:
    """瓦片数不多时，直接构建简单根节点。"""
    children = []
    for tile in tiles:
        children.append({
            "boundingVolume": tile.bounding_volume,
            "geometricError": round(tile.geometric_error, 2),
            "refine": config.refine_mode.value,
            "content": {"uri": tile.content_uri},
        })

    root_bbox = compute_merged_bbox(tiles)
    root_error = max(t.geometric_error for t in tiles) * 2.0

    return {
        "boundingVolume": root_bbox,
        "geometricError": round(root_error, 2),
        "refine": config.refine_mode.value,
        "children": children,
    }


def _assemble_glb_safe(assembler: GlbAssembler, meshes: List[OsgeMesh], name: str, use_draco: bool) -> bytes:
    """安全组装 GLB，支持 Draco 条件控制。"""
    original = assembler.config.mesh_compression
    assembler.config.mesh_compression = use_draco
    try:
        gltf_json, bin_data = assembler.build_gltf(meshes, tile_name=name)
        return pack_glb(gltf_json, bin_data)
    finally:
        assembler.config.mesh_compression = original


def _package_tile(glb_bytes: bytes, config: ConvertConfig) -> bytes:
    """根据版本封装瓦片。"""
    if config.tiles_version == "1.0":
        return package_to_b3dm(glb_bytes)
    return glb_bytes

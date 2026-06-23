"""网格简化模块。

基于 meshoptimizer 库实现顶点缓存优化、过度绘制减少和自适应网格简化。
支持多级 LOD 生成，与 Draco 压缩条件联动。
"""

from dataclasses import dataclass
from typing import List, Optional

import numpy as np

try:
    import meshoptimizer
except ImportError:
    meshoptimizer = None


@dataclass
class SimplifyResult:
    """简化后的网格数据。"""
    vertices: np.ndarray    # (N, 3) float32
    normals: np.ndarray     # (N, 3) float32
    uvs: np.ndarray         # (N, 2) float32
    indices: np.ndarray     # (M,) uint32
    triangle_ratio: float   # 实际三角形比例
    texture_data: Optional[bytes] = None
    texture_path: Optional[str] = None


def simplify_mesh(
    vertices: np.ndarray,
    normals: np.ndarray,
    uvs: np.ndarray,
    indices: np.ndarray,
    target_ratio: float = 1.0,
    target_error: float = 0.01,
    optimize: bool = True,
) -> SimplifyResult:
    """对单个网格执行简化。

    Args:
        vertices: 顶点位置数组 (N, 3)
        normals: 法线数组 (N, 3)
        uvs: 纹理坐标数组 (N, 2)
        indices: 索引数组 (M,)
        target_ratio: 目标三角形比例 (0.0~1.0)，1.0 表示不简化
        target_error: 最大允许误差（归一化坐标系下）
        optimize: 是否执行顶点缓存/过度绘制优化

    Returns:
        SimplifyResult 包含简化后的网格数据
    """
    if meshoptimizer is None:
        raise RuntimeError("meshoptimizer 未安装，请执行: pip install meshoptimizer")

    if len(indices) == 0 or len(vertices) == 0:
        return SimplifyResult(
            vertices=vertices, normals=normals, uvs=uvs, indices=indices,
            triangle_ratio=1.0,
        )

    # 比例接近 1.0 时跳过简化，仅做优化
    original_tri_count = len(indices) // 3
    target_index_count = int(len(indices) * target_ratio)
    target_index_count = max(target_index_count, 3 * 3)  # 至少保留 3 个三角形

    if target_ratio >= 0.999:
        result_indices = indices.copy()
        result_ratio = 1.0
    else:
        # meshoptimizer.simplify 需要连续的 float32 顶点位置
        pos = np.ascontiguousarray(vertices, dtype=np.float32)
        idx = np.ascontiguousarray(indices, dtype=np.uint32)

        destination = np.empty_like(idx)
        result_error_arr = np.zeros(1, dtype=np.float32)

        new_count = meshoptimizer.simplify(
            destination,
            idx,
            pos,
            target_index_count=target_index_count,
            target_error=target_error,
            options=0,
            result_error=result_error_arr,
        )

        result_indices = destination[:new_count].copy()
        result_ratio = new_count / len(indices) if len(indices) > 0 else 0.0

    # 顶点缓存优化 + 过度绘制减少
    if optimize and len(result_indices) > 0:
        result_indices = _optimize_indices(result_indices)

    # 根据简化后的索引重建顶点数组（去重未引用的顶点）
    out_verts, out_normals, out_uvs, out_indices = _compact_mesh(
        vertices, normals, uvs, result_indices
    )

    return SimplifyResult(
        vertices=out_verts,
        normals=out_normals,
        uvs=out_uvs,
        indices=out_indices,
        triangle_ratio=result_ratio,
    )


def generate_lod_meshes(
    vertices: np.ndarray,
    normals: np.ndarray,
    uvs: np.ndarray,
    indices: np.ndarray,
    lod_ratios: List[float],
    target_error: float = 0.01,
) -> List[SimplifyResult]:
    """根据 LOD 比例数组生成多级简化网格。

    Args:
        vertices: 原始顶点 (N, 3)
        normals: 原始法线 (N, 3)
        uvs: 原始 UV (N, 2)
        indices: 原始索引 (M,)
        lod_ratios: LOD 级别比例列表，如 [1.0, 0.5, 0.25]
        target_error: 简化误差阈值

    Returns:
        每个 LOD 级别的 SimplifyResult 列表（从高精度到低精度）
    """
    results = []
    for ratio in lod_ratios:
        result = simplify_mesh(
            vertices, normals, uvs, indices,
            target_ratio=ratio,
            target_error=target_error,
            optimize=True,
        )
        results.append(result)
    return results


def _optimize_indices(indices: np.ndarray) -> np.ndarray:
    """对索引执行顶点缓存优化。"""
    idx = np.ascontiguousarray(indices, dtype=np.uint32)
    destination = np.empty_like(idx)

    # 顶点缓存优化：重排三角形顺序以提升 GPU 缓存命中率
    meshoptimizer.optimize_vertex_cache(destination, idx)

    return destination


def _compact_mesh(
    vertices: np.ndarray,
    normals: np.ndarray,
    uvs: np.ndarray,
    indices: np.ndarray,
) -> tuple:
    """根据索引重建紧凑的顶点数组，移除未引用的顶点。"""
    if len(indices) == 0:
        return (
            np.empty((0, 3), dtype=np.float32),
            np.empty((0, 3), dtype=np.float32),
            np.empty((0, 2), dtype=np.float32),
            np.array([], dtype=np.uint32),
        )

    # 找出被引用的顶点索引
    used_indices = np.unique(indices)
    remap = np.full(len(vertices), -1, dtype=np.int32)
    remap[used_indices] = np.arange(len(used_indices), dtype=np.int32)

    new_vertices = vertices[used_indices].copy()
    new_normals = normals[used_indices].copy() if len(normals) >= len(vertices) else normals.copy()
    new_uvs = uvs[used_indices].copy() if len(uvs) >= len(vertices) else uvs.copy()
    new_indices = remap[indices].astype(np.uint32)

    return new_vertices, new_normals, new_uvs, new_indices

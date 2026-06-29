"""优化流水线：多阶段并行处理 OSGB → 3D Tiles。

状态机：
  PARSE → SIMPLIFY → ASSEMBLE → COMPRESS_TEXTURE → WRITE → DONE

每个阶段内部使用 ProcessPoolExecutor 并行处理独立瓦片。
阶段间通过 PipelineContext 传递数据。

LOD × Simplify × Draco 联动矩阵：
┌─────────┬──────────────┬─────────┬────────────────────────────────────┐
│  LOD    │  simplify    │ draco   │  行为                              │
├─────────┼──────────────┼─────────┼────────────────────────────────────┤
│  ON     │  ON          │ ON      │ LOD0 原始网格, LOD1 50%简化+Draco  │
│  ON     │  ON          │ OFF     │ LOD0 原始网格, LOD1 50%简化        │
│  ON     │  OFF         │ ON      │ LOD0 原始网格, LOD1+Draco          │
│  ON     │  OFF         │ OFF     │ 仅结构分层，不简化不压缩            │
│  OFF    │  ON          │ ON      │ 单级简化+Draco                     │
│  OFF    │  ON          │ OFF     │ 单级简化                           │
│  OFF    │  OFF         │ ON      │ 单级 Draco                         │
│  OFF    │  OFF         │ OFF     │ 标准转换                           │
└─────────┴──────────────┴─────────┴────────────────────────────────────┘

Draco 条件联动（LOD 模式）：
- LOD0（最高质量，ratio=1.0）：永远不压缩，避免近景精度损失
- LOD1/LOD2：根据 --enable-draco 决定是否压缩
"""

import gc
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional

import numpy as np

from .config import ConvertConfig
from .memory_guard import release_numpy_refs


class PipelineStage(Enum):
    """流水线阶段枚举。"""
    PARSE = auto()
    SIMPLIFY = auto()
    ASSEMBLE = auto()
    COMPRESS_TEXTURE = auto()
    WRITE = auto()
    DONE = auto()


@dataclass
class TileWorkItem:
    """单个瓦片的工作单元。"""
    tile_id: int
    osgb_path: str
    tile_name: str
    output_dir: str
    tile_subdir: str
    depth: int
    parent_geometric_error: float


@dataclass
class TileResult:
    """单个瓦片的处理结果。"""
    tile_id: int
    tile_json: dict
    tile_bytes: bytes
    tile_rel_path: str
    lod_level: Optional[int] = None
    tri_count: int = 0
    file_size: int = 0


@dataclass
class PipelineContext:
    """流水线上下文，在所有阶段间共享。"""
    config: ConvertConfig
    metadata: object  # OsgeMetadata
    structure_type: object  # StructureType
    ecef_matrix: np.ndarray
    assembler: object  # GlbAssembler
    tile_counter: int = 0


class OptimizationPipeline:
    """工业级优化流水线。

    核心原则：
    1. LOD0（最高质量）永远不压缩 Draco
    2. LOD1+ 根据 --enable-draco 决定是否压缩
    3. 纹理压缩（KTX2）独立于 Draco 开关
    4. 每个阶段结束后主动释放 numpy 缓冲区
    """

    def __init__(self, context: PipelineContext):
        self.ctx = context
        self._stage = PipelineStage.PARSE

    @property
    def stage(self) -> PipelineStage:
        return self._stage

    def process_tile(self, work: TileWorkItem, parser) -> List[TileResult]:
        """处理单个瓦片的完整流水线。

        返回可能多个 TileResult（LOD 模式下每个级别一个）。
        """
        self._stage = PipelineStage.PARSE
        node = parser.parse_file(work.osgb_path)

        self._stage = PipelineStage.SIMPLIFY
        meshes = node.meshes
        if not meshes:
            self._stage = PipelineStage.DONE
            return []

        # 加载纹理
        osgb_dir = os.path.dirname(work.osgb_path)
        for mesh in meshes:
            if mesh.texture_path and mesh.texture_data is None:
                tex_path = os.path.join(osgb_dir, mesh.texture_path)
                if not os.path.isabs(mesh.texture_path) and not os.path.exists(tex_path):
                    tex_path = mesh.texture_path
                from .texture import load_texture
                mesh.texture_data = load_texture(tex_path)

        # 逐顶点坐标纠正
        if self.ctx.config.precise_coords:
            self._apply_precise_coords(meshes)

        config = self.ctx.config

        if config.enable_lod:
            results = self._process_lod_tiles(meshes, work)
        else:
            results = self._process_single_tile(meshes, work)

        # 内存回收
        release_numpy_refs(meshes)
        gc.collect()

        self._stage = PipelineStage.DONE
        return results

    def _process_lod_tiles(
        self, meshes: List, work: TileWorkItem
    ) -> List[TileResult]:
        """LOD 模式：为每个级别生成独立 GLB。

        关键规则：
        - LOD0（ratio=1.0, 最高质量）：永远不压缩 Draco
        - LOD1+：根据 enable_draco 决定
        - 纹理尺寸随 LOD 级别递减
        """
        from .mesh_simplifier import generate_lod_meshes
        from .osgb_parser import OsgeMesh

        config = self.ctx.config
        lod_levels = config.lod_levels  # [1.0, 0.5, 0.25]

        # 计算每级纹理尺寸
        lod_texture_sizes = self._compute_lod_texture_sizes(lod_levels)

        # 为每个网格生成多级 LOD
        all_lod_meshes = []
        for mesh in meshes:
            lod_results = generate_lod_meshes(
                mesh.vertices, mesh.normals, mesh.uvs, mesh.indices,
                lod_ratios=lod_levels,
                target_error=config.simplify_error,
            )
            for level_idx, result in enumerate(lod_results):
                result.texture_data = mesh.texture_data
                result.texture_path = mesh.texture_path
                result.lod_texture_size = lod_texture_sizes[level_idx]
            all_lod_meshes.append(lod_results)

        # 构建每个 LOD 级别的 tile
        # 从最低精度到最高精度构建嵌套树
        tiles_by_level = []
        shared_bv = self._compute_bounding_volume_from_meshes(meshes)

        for level_idx in range(len(lod_levels)):
            self._stage = PipelineStage.ASSEMBLE

            # 构建该级别的 OsgeMesh 列表
            level_meshes = []
            for mesh_lods in all_lod_meshes:
                r = mesh_lods[level_idx]
                level_meshes.append(OsgeMesh(
                    vertices=r.vertices, normals=r.normals,
                    uvs=r.uvs, indices=r.indices,
                    texture_data=r.texture_data,
                    texture_path=r.texture_path,
                    lod_texture_size=r.lod_texture_size,
                ))

            # Draco 条件联动：LOD0 永不压缩
            is_highest_detail = (level_idx == 0)
            use_draco = config.enable_draco and not is_highest_detail

            glb_bytes = self._assemble_glb(level_meshes, work.tile_name, use_draco)
            tile_bytes = self._package_tile(glb_bytes)

            # 内存回收
            release_numpy_refs(level_meshes)

            self.ctx.tile_counter += 1
            suffix = f"lod{level_idx}"
            tile_name_str = f"{work.tile_name}_{suffix}_{self.ctx.tile_counter:04d}{self._tile_extension}"
            tile_rel_path = tile_name_str

            # 几何误差
            if level_idx == 0:
                geo_error = 0.0  # LOD0（叶子）：近景加载，误差为 0
            else:
                geo_error = work.parent_geometric_error * (1.0 / lod_levels[level_idx])

            tri_count = sum(len(m.indices) // 3 for m in level_meshes)
            draco_tag = "+Draco" if use_draco else ""
            tex_size = lod_texture_sizes[level_idx]
            print(f" → LOD{level_idx}: {tile_name_str} ({len(tile_bytes)/1024:.1f}KB, {tri_count}tris, tex={tex_size}px {draco_tag})")

            tiles_by_level.append({
                "tile_json": {
                    "geometricError": round(geo_error, 2),
                    "refine": config.refine_mode.value,
                    "boundingVolume": shared_bv,
                    "content": {"uri": tile_rel_path},
                },
                "tile_bytes": tile_bytes,
                "tile_rel_path": tile_rel_path,
                "lod_level": level_idx,
                "tri_count": tri_count,
            })

        # 组装嵌套 LOD 树：Root（最粗糙）→ Leaf（最精细）
        # tiles_by_level[0] = LOD0（最精细）= 叶子
        # tiles_by_level[-1] = LODn-1（最粗糙）= 根
        # 需要反转：根在前，叶在后
        results = []
        for i, item in enumerate(tiles_by_level):
            self.ctx.tile_counter += 0  # 不增加，已在上面增加过
            results.append(TileResult(
                tile_id=work.tile_id,
                tile_json=item["tile_json"],
                tile_bytes=item["tile_bytes"],
                tile_rel_path=item["tile_rel_path"],
                lod_level=item["lod_level"],
                tri_count=item["tri_count"],
                file_size=len(item["tile_bytes"]),
            ))

        return results

    def _process_single_tile(
        self, meshes: List, work: TileWorkItem
    ) -> List[TileResult]:
        """标准模式：单个 GLB 输出。"""
        self._stage = PipelineStage.ASSEMBLE
        config = self.ctx.config
        use_draco = config.enable_draco

        glb_bytes = self._assemble_glb(meshes, work.tile_name, use_draco)
        tile_bytes = self._package_tile(glb_bytes)

        self.ctx.tile_counter += 1
        tile_name_str = f"{work.tile_name}_{self.ctx.tile_counter:04d}{self._tile_extension}"
        tile_rel_path = tile_name_str

        tri_count = sum(len(m.indices) // 3 for m in meshes)
        draco_tag = "+Draco" if use_draco else ""
        print(f" → {tile_name_str} ({len(tile_bytes)/1024:.1f}KB, {tri_count}tris {draco_tag})")

        return [TileResult(
            tile_id=work.tile_id,
            tile_json={"content": {"uri": tile_rel_path}},
            tile_bytes=tile_bytes,
            tile_rel_path=tile_rel_path,
            tri_count=tri_count,
            file_size=len(tile_bytes),
        )]

    def _assemble_glb(self, meshes, tile_name, use_draco):
        """组装 GLB，按参数控制 Draco。"""
        from .gltf_assembler import GlbAssembler, pack_glb

        # 创建独立的 assembler 副本，避免修改共享状态
        tile_config = ConvertConfig(
            back_face_culling=self.ctx.config.back_face_culling,
            force_double_sided=self.ctx.config.force_double_sided,
            unlit_shading=self.ctx.config.unlit_shading,
            texture_format=self.ctx.config.texture_format,
            mesh_compression=use_draco,
            output_format=self.ctx.config.output_format,
            refine_mode=self.ctx.config.refine_mode,
            geometric_error_scale=self.ctx.config.geometric_error_scale,
            max_texture_size=self.ctx.config.max_texture_size,
            ecef_transform=self.ctx.config.ecef_transform,
            enable_lod=self.ctx.config.enable_lod,
            enable_simplify=self.ctx.config.enable_simplify,
            lod_levels=self.ctx.config.lod_levels,
            simplify_error=self.ctx.config.simplify_error,
            enable_draco=use_draco,
            enable_texture_compress=self.ctx.config.enable_texture_compress,
            tiles_version=self.ctx.config.tiles_version,
        )
        assembler = GlbAssembler(tile_config)
        gltf_json, bin_data = assembler.build_gltf(meshes, tile_name=tile_name)
        return pack_glb(gltf_json, bin_data)

    def _package_tile(self, glb_bytes):
        """根据版本将 glb 数据封装为最终瓦片格式。"""
        config = self.ctx.config
        if config.tiles_version == "1.0":
            from .b3dm import package_to_b3dm
            return package_to_b3dm(glb_bytes)
        return glb_bytes

    @property
    def _tile_extension(self):
        """根据版本返回瓦片文件扩展名。"""
        return ".b3dm" if self.ctx.config.tiles_version == "1.0" else ".glb"

    def _compute_lod_texture_sizes(self, lod_levels):
        """计算每个 LOD 级别对应的纹理尺寸。

        纹理尺寸随 LOD 级别单调递减，最小 64px，对齐到 2 的幂次。
        """
        config = self.ctx.config
        sizes = []
        for ratio in lod_levels:
            if ratio >= 0.999:
                sizes.append(config.max_texture_size)
            else:
                scale = max(ratio, 0.125)
                size = int(config.max_texture_size * scale)
                # 向下对齐到 2 的幂次
                p = 1
                while p * 2 <= size:
                    p <<= 1
                p = max(p, 64)
                sizes.append(min(p, config.max_texture_size))
        # 确保单调递减
        for i in range(1, len(sizes)):
            if sizes[i] >= sizes[i - 1]:
                sizes[i] = max(sizes[i - 1] // 2, 64)
        return sizes

    def _compute_bounding_volume_from_meshes(self, meshes) -> dict:
        """从网格列表计算 OBB 包围体。"""
        all_verts = []
        for m in meshes:
            if len(m.vertices) > 0:
                all_verts.append(m.vertices)
        if all_verts:
            verts = np.concatenate(all_verts)
            center = verts.mean(axis=0)
            half_size = (verts.max(axis=0) - verts.min(axis=0)) / 2.0
            return {
                "box": [
                    float(center[0]), float(center[1]), float(center[2]),
                    float(half_size[0]), 0.0, 0.0,
                    0.0, float(half_size[1]), 0.0,
                    0.0, 0.0, float(half_size[2]),
                ]
            }
        return {"sphere": [0.0, 0.0, 0.0, 1.0]}

    def _apply_precise_coords(self, meshes):
        """逐顶点坐标纠正：将顶点从源坐标系精确转换为 ECEF 局部 ENU。

        与单一 ECEF 变换不同，此方法对每个顶点独立执行完整的
        CRS → WGS84 → 大地水准面纠正 → ECEF → 局部 ENU 变换，
        消除投影畸变带来的位置误差。
        """
        from pyproj import Transformer

        metadata = self.ctx.metadata
        srs = metadata.srs

        # 创建坐标变换器
        crs_to_wgs84 = Transformer.from_crs(srs, "EPSG:4326", always_xy=True)
        wgs84_to_ecef = Transformer.from_crs("EPSG:4326", "EPSG:4978", always_xy=True)

        # 原点 ECEF 坐标（用于 ENU 参考点）
        origin_ecef = wgs84_to_ecef.transform(
            metadata.origin_lon, metadata.origin_lat, metadata.origin_height
        )

        # ENU 旋转矩阵（ECEF → ENU）
        lon_rad = math.radians(metadata.origin_lon)
        lat_rad = math.radians(metadata.origin_lat)
        cos_lat, sin_lat = math.cos(lat_rad), math.sin(lat_rad)
        cos_lon, sin_lon = math.cos(lon_rad), math.sin(lon_rad)

        for mesh in meshes:
            if len(mesh.vertices) == 0:
                continue

            verts = mesh.vertices.copy()

            # CRS 轴顺序修正：(Northing, Easting) → (Easting, Northing)
            if metadata.swap_xy:
                verts[:, [0, 1]] = verts[:, [1, 0]]

            # 1. 源坐标系 → WGS84（批量转换，极快）
            lon, lat = crs_to_wgs84.transform(verts[:, 0], verts[:, 1])
            h = verts[:, 2]

            # 2. 大地水准面纠正
            if abs(metadata.geoid_offset) > 0.01:
                h = h + metadata.geoid_offset

            # 3. WGS84 → ECEF
            ecef_x, ecef_y, ecef_z = wgs84_to_ecef.transform(lon, lat, h)

            # 4. ECEF → 局部 ENU（相对于原点）
            dx = ecef_x - origin_ecef[0]
            dy = ecef_y - origin_ecef[1]
            dz = ecef_z - origin_ecef[2]

            enu_x = -sin_lon * dx + cos_lon * dy
            enu_y = -sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz
            enu_z = cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz

            mesh.vertices[:, 0] = enu_x
            mesh.vertices[:, 1] = enu_y
            mesh.vertices[:, 2] = enu_z


# ── 并发调度器 ──

def run_parallel_pipeline(
    work_items: List[TileWorkItem],
    context: PipelineContext,
    parser,
) -> Dict[int, List[TileResult]]:
    """使用 ProcessPoolExecutor 并行处理多个瓦片。

    对于少量瓦片（≤2），直接串行处理以避免进程池启动开销。
    对于大量瓦片，使用 ProcessPoolExecutor 并行处理。

    注意：子进程中重建 GlbAssembler 和 OsgeBinaryParser，
    避免跨进程共享状态。
    """
    max_workers = context.config.threads
    results: Dict[int, List[TileResult]] = {}

    if len(work_items) <= 2:
        pipeline = OptimizationPipeline(context)
        for work in work_items:
            tile_results = pipeline.process_tile(work, parser)
            results[work.tile_id] = tile_results
        return results

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_work = {}
        for work in work_items:
            future = executor.submit(
                _process_tile_in_subprocess,
                work, context.config, context.metadata,
                context.structure_type, context.ecef_matrix,
            )
            future_to_work[future] = work

        for future in as_completed(future_to_work):
            work = future_to_work[future]
            try:
                tile_results = future.result()
                results[work.tile_id] = tile_results
            except Exception as e:
                print(f"  [错误] 瓦片 {work.tile_name} 处理失败: {e}", file=sys.stderr)
                results[work.tile_id] = []

    return results


def _process_tile_in_subprocess(
    work: TileWorkItem,
    config: ConvertConfig,
    metadata,
    structure_type,
    ecef_matrix,
) -> List[TileResult]:
    """子进程入口：重建局部上下文并处理单个瓦片。"""
    from .gltf_assembler import GlbAssembler
    from .osgb_parser import OsgeBinaryParser

    assembler = GlbAssembler(config)
    parser = OsgeBinaryParser(config)

    ctx = PipelineContext(
        config=config,
        metadata=metadata,
        structure_type=structure_type,
        ecef_matrix=ecef_matrix,
        assembler=assembler,
    )
    pipeline = OptimizationPipeline(ctx)
    return pipeline.process_tile(work, parser)

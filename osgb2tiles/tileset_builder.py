"""3D Tiles 1.1 tileset.json 构建器。

递归遍历 OSGB 瓦片树，生成符合 3D Tiles 1.1 规范的 tileset.json 与 GLB 瓦片内容。
支持 LOD 多级简化与 Draco 条件压缩联动。
"""

import json
import os
import sys
from typing import List, Optional

import numpy as np

from .config import ConvertConfig, RefineMode
from .gltf_assembler import GlbAssembler
from .metadata import OsgeMetadata, local_to_ecef_transform, bbox_center_lonlat
from .osgb_parser import OsgeTileNode, OsgeBinaryParser, OsgeMesh, PageLODInfo, compute_geometric_error
from .spatial_quadtree import TileRecord
from .structure import StructureType, resolve_pagelod_path, extract_level_from_filename, compute_level_based_error
from .texture import load_texture


class TilesetBuilder:
    """递归构建 3D Tiles 1.1 tileset.json。"""

    def __init__(self, config: ConvertConfig, metadata: OsgeMetadata,
                 structure_type: StructureType = StructureType.CONTEXT_CAPTURE):
        self.config = config
        self.metadata = metadata
        self.structure_type = structure_type
        self.ecef_matrix = local_to_ecef_transform(metadata)
        self.parser = OsgeBinaryParser(config)
        self.assembler = GlbAssembler(config)
        self.tile_counter = 0
        self._root_name_length = 0
        self._base_level = 0  # 文件名中 _Lxx_ 的最小层级
        self._range_max_uniform = True  # 所有 PagedLOD 的 range_max 是否相同

    def build(self, root_osgb: str, output_dir: str):
        """主入口：从根 OSGB 文件构建完整 3D Tiles 输出。

        输出结构：
        output_dir/
          tileset.json              (根 tileset，children 指向子 tileset)
          Data/
            Tile_+000_+000/
              tileset.json          (子 tileset，包含完整瓦片树)
              *.glb / *.b3dm        (瓦片文件)
            Tile_+000_+001/
              ...
        """
        os.makedirs(output_dir, exist_ok=True)
        data_dir = os.path.join(output_dir, "Data")
        os.makedirs(data_dir, exist_ok=True)

        if self.structure_type == StructureType.DJI_TERRA:
            self._root_name_length = len(os.path.splitext(os.path.basename(root_osgb))[0])

        print(f"  解析根文件: {os.path.basename(root_osgb)}")
        root_node = self.parser.parse_file(root_osgb)

        # 预扫描：提取基准层级 & 检测 range_max 是否一致
        self._base_level, self._range_max_uniform = self._scan_level_range(root_node)

        # 收集顶层瓦片组，为每个生成独立子 tileset
        osgb_dir = os.path.dirname(root_osgb)
        top_level_paths = []

        # 从 PagedLOD 收集顶层子节点路径
        for pagelod in root_node.page_lods:
            child_path = resolve_pagelod_path(
                osgb_dir, pagelod.child_tile_path, self.structure_type, root_osgb
            )
            if os.path.exists(child_path):
                top_level_paths.append(child_path)

        # 如果根只有一个子节点（中间容器），递归找到真正的顶层瓦片组
        if len(top_level_paths) == 1:
            container_node = self.parser.parse_file(top_level_paths[0])
            container_dir = os.path.dirname(top_level_paths[0])
            deeper_paths = []
            for pagelod in container_node.page_lods:
                child_path = resolve_pagelod_path(
                    container_dir, pagelod.child_tile_path, self.structure_type, top_level_paths[0]
                )
                if os.path.exists(child_path):
                    deeper_paths.append(child_path)
            if deeper_paths:
                top_level_paths = deeper_paths

        sub_tileset_refs = []

        for child_osgb_path in top_level_paths:
            tile_name = os.path.splitext(os.path.basename(child_osgb_path))[0]
            tile_subdir = os.path.join("Data", tile_name)
            os.makedirs(os.path.join(output_dir, tile_subdir), exist_ok=True)

            # 优先使用 Data/<tile_name>/ 目录下的同名文件（包含完整层级链）
            alt_path = os.path.join(osgb_dir, tile_name, os.path.basename(child_osgb_path))
            if os.path.exists(alt_path):
                child_osgb_path = alt_path

            child_node = self.parser.parse_file(child_osgb_path)
            self._load_mesh_textures(child_node, os.path.dirname(child_osgb_path))
            sub_tile = self._process_node(
                child_node, child_osgb_path, output_dir, depth=0,
                tile_subdir=tile_subdir,
            )

            # 生成子 tileset.json（移除 transform，避免重复坐标变换）
            sub_tile.pop("transform", None)
            sub_tileset = {
                "asset": {
                    "version": self.config.tiles_version,
                    "generator": "OSGB2Tiles v1.0",
                    "gltfUpAxis": "Z",
                },
                "geometricError": sub_tile.get("geometricError", 1000),
                "root": sub_tile,
            }
            sub_ts_path = os.path.join(output_dir, tile_subdir, "tileset.json")
            with open(sub_ts_path, "w", encoding="utf-8") as f:
                json.dump(sub_tileset, f, indent=2, ensure_ascii=False)

            sub_tileset_refs.append({
                "boundingVolume": sub_tile.get("boundingVolume", {}),
                "geometricError": sub_tile.get("geometricError", 1000),
                "content": {"uri": f"./{tile_subdir}/tileset.json"},
            })

            glb_count = self._count_glb_files(sub_tile)
            print(f"  子 tileset: {tile_subdir}/ ({glb_count} 瓦片)")

        # 生成根 tileset.json
        root_error = self._compute_root_error(root_node)
        # 合并所有子 tileset 的包围体作为根包围体
        all_bvs = [ref["boundingVolume"] for ref in sub_tileset_refs if "boundingVolume" in ref]
        root_bv = self._merge_bounding_volumes(all_bvs) if all_bvs else {"sphere": [0, 0, 0, 1]}
        root_tileset = {
            "asset": {
                "version": self.config.tiles_version,
                "generator": "OSGB2Tiles v1.0",
            },
            "geometricError": root_error,
            "root": {
                "boundingVolume": root_bv,
                "geometricError": root_error,
                "refine": self.config.refine_mode.value,
                "children": sub_tileset_refs,
            },
        }

        # 根节点应用 ECEF 变换
        if self.config.ecef_transform:
            root_tileset["root"]["transform"] = self.ecef_matrix.T.flatten().tolist()

        tileset_path = os.path.join(output_dir, "tileset.json")
        with open(tileset_path, "w", encoding="utf-8") as f:
            json.dump(root_tileset, f, indent=2, ensure_ascii=False)

        return tileset_path

    def _merge_bounding_volumes(self, bvs: list) -> dict:
        """合并多个包围体为一个大的包围盒。"""
        import numpy as np
        if not bvs:
            return {"sphere": [0, 0, 0, 1]}
        all_centers = []
        all_half_sizes = []
        for bv in bvs:
            if "box" in bv:
                b = bv["box"]
                all_centers.append([b[0], b[1], b[2]])
                all_half_sizes.append([b[3], b[7], b[11]])
            elif "sphere" in bv:
                s = bv["sphere"]
                all_centers.append([s[0], s[1], s[2]])
                all_half_sizes.append([s[3], s[3], s[3]])
        if not all_centers:
            return {"sphere": [0, 0, 0, 1]}
        centers = np.array(all_centers)
        half_sizes = np.array(all_half_sizes)
        min_corner = (centers - half_sizes).min(axis=0)
        max_corner = (centers + half_sizes).max(axis=0)
        center = (min_corner + max_corner) / 2.0
        half = (max_corner - min_corner) / 2.0
        return {
            "box": [
                float(center[0]), float(center[1]), float(center[2]),
                float(half[0]), 0.0, 0.0,
                0.0, float(half[1]), 0.0,
                0.0, 0.0, float(half[2]),
            ]
        }

    def _count_glb_files(self, tile: dict) -> int:
        """递归统计瓦片树中的 GLB/B3DM 文件数。"""
        count = 0
        if "content" in tile:
            count += 1
        for child in tile.get("children", []):
            count += self._count_glb_files(child)
        return count

    # ─────────────────────────────────────────────
    #  流水线委托（已迁移至 pipeline.py）
    # ─────────────────────────────────────────────

    def process_mesh_pipeline(
        self,
        meshes: List[OsgeMesh],
        tile_name: str,
        output_dir: str,
        parent_geometric_error: float,
    ) -> dict:
        """多参数联动核心调度函数（委托给 OptimizationPipeline）。"""
        if not meshes:
            return {}

        from .pipeline import OptimizationPipeline, PipelineContext, TileWorkItem

        work = TileWorkItem(
            tile_id=self.tile_counter,
            osgb_path="",
            tile_name=tile_name,
            output_dir=output_dir,
            tile_subdir="tiles",
            depth=0,
            parent_geometric_error=parent_geometric_error,
        )
        ctx = PipelineContext(
            config=self.config,
            metadata=self.metadata,
            structure_type=self.structure_type,
            ecef_matrix=self.ecef_matrix,
            assembler=self.assembler,
            tile_counter=self.tile_counter,
        )
        pipeline = OptimizationPipeline(ctx)
        results = pipeline.process_tile(work, self.parser)
        self.tile_counter = ctx.tile_counter

        if not results:
            return {}

        # 写入瓦片文件
        for r in results:
            full_path = os.path.join(output_dir, r.tile_rel_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(r.tile_bytes)

        # 组装返回值
        if self.config.enable_lod and len(results) > 1:
            # 构建嵌套 LOD 树
            reversed_results = list(reversed(results))
            root_tile_json = reversed_results[0].tile_json
            current = root_tile_json
            for r in reversed_results[1:]:
                current["children"] = [r.tile_json]
                current = r.tile_json
            return root_tile_json
        return results[0].tile_json

    # ─────────────────────────────────────────────
    #  标准节点处理
    # ─────────────────────────────────────────────

    def _process_node(
        self,
        node: OsgeTileNode,
        osgb_path: str,
        output_dir: str,
        depth: int,
        tile_subdir: str = "tiles",
    ) -> dict:
        """递归处理单个节点，生成 tile JSON + GLB 内容。

        Args:
            tile_subdir: GLB 文件输出子目录（如 'Data/Tile_+000_+000'）
        """
        tile = {}

        self.tile_counter += 1
        tile_name = os.path.basename(osgb_path)
        meshes_count = len(node.meshes)
        children_count = len(node.page_lods)
        print(f"  [{self.tile_counter}] 处理: {tile_name} (深度={depth}, 网格={meshes_count}, 子节点={children_count})", end="")
        sys.stdout.flush()

        # 1. 包围体
        tile["boundingVolume"] = self._compute_bounding_volume(node)

        # 2. 几何误差（基于文件名 _Lxx_ 层级标识的指数衰减）
        # 参考工具使用包围体平均半尺寸作为 base_error，0.5 为每级衰减因子
        level = self._extract_lod_level(osgb_path)
        if level is None and node.page_lods:
            # 文件名无 _Lxx_ → 从子节点推断层级
            child_level = self._extract_lod_level_from_path(node.page_lods[0].child_tile_path)
            if child_level is not None:
                level = child_level
        if level is not None and depth > 0:
            # 非根节点且有 _Lxx_ → 从包围体计算 base_error 并衰减
            bv = tile.get("boundingVolume", {})
            if "box" in bv:
                b = bv["box"]
                base_error = (abs(b[3]) + abs(b[7]) + abs(b[11])) / 3.0
            elif "sphere" in bv:
                base_error = bv["sphere"][3]
            else:
                base_error = 100.0
            if self._base_level <= 0:
                self._base_level = level
            offset = max(level - self._base_level, 0)
            tile["geometricError"] = max(base_error * (0.5 ** offset), 0.01)
        elif node.page_lods:
            # 根节点或无 _Lxx_ → 使用 range_max
            tile["geometricError"] = compute_geometric_error(
                node.page_lods[0], self.config.geometric_error_scale
            )
        else:
            # 叶子节点
            level = extract_level_from_filename(
                os.path.basename(osgb_path), self.structure_type, self._root_name_length
            )
            if level is not None:
                tile["geometricError"] = compute_level_based_error(
                    level, self.config.geometric_error_scale
                )
            else:
                tile["geometricError"] = max(100.0 / (depth + 1), 0.01)

        # 3. 根节点应用 ECEF 变换
        if depth == 0 and self.config.ecef_transform:
            tile["transform"] = self.ecef_matrix.T.flatten().tolist()

        # 4. 细化模式
        tile["refine"] = self.config.refine_mode.value

        # 5. 加载纹理数据
        osgb_dir = os.path.dirname(osgb_path)
        self._load_mesh_textures(node, osgb_dir)

        # 6. 递归处理 OSGB PagedLOD 子节点（先于 LOD 树构建）
        osgb_children = []
        for child in node.children:
            child_tile = self._process_node(child, osgb_path, output_dir, depth + 1, tile_subdir)
            osgb_children.append(child_tile)

        for pagelod in node.page_lods:
            child_path = resolve_pagelod_path(
                osgb_dir, pagelod.child_tile_path, self.structure_type, osgb_path
            )
            if os.path.exists(child_path):
                child_node = self.parser.parse_file(child_path)
                child_tile = self._process_node(child_node, child_path, output_dir, depth + 1, tile_subdir)
                osgb_children.append(child_tile)

        # 7. 生成 GLB（委托给 OptimizationPipeline）
        if node.meshes:
            from .pipeline import OptimizationPipeline, PipelineContext, TileWorkItem

            work = TileWorkItem(
                tile_id=self.tile_counter,
                osgb_path=osgb_path,
                tile_name=node.name,
                output_dir=output_dir,
                tile_subdir=tile_subdir,
                depth=depth,
                parent_geometric_error=tile.get("geometricError", 100),
            )
            ctx = PipelineContext(
                config=self.config,
                metadata=self.metadata,
                structure_type=self.structure_type,
                ecef_matrix=self.ecef_matrix,
                assembler=self.assembler,
                tile_counter=self.tile_counter,
            )
            pipeline = OptimizationPipeline(ctx)
            results = pipeline.process_tile(work, self.parser)
            self.tile_counter = ctx.tile_counter

            if results:
                # 写入瓦片文件
                for r in results:
                    full_path = os.path.join(output_dir, tile_subdir, r.tile_rel_path)
                    os.makedirs(os.path.dirname(full_path), exist_ok=True)
                    with open(full_path, "wb") as f:
                        f.write(r.tile_bytes)

                # 组装 tile JSON
                if self.config.enable_lod and len(results) > 1:
                    # 构建嵌套 LOD 树：Root（最粗糙）→ Leaf（最精细）
                    # results 顺序：[LOD0, LOD1, LOD2]（从高精度到低精度）
                    # 嵌套：LOD2(Root) → LOD1 → LOD0(Leaf)
                    reversed_results = list(reversed(results))
                    root_tile_json = reversed_results[0].tile_json
                    current = root_tile_json
                    for r in reversed_results[1:]:
                        current["children"] = [r.tile_json]
                        current = r.tile_json
                    if osgb_children:
                        current["children"] = current.get("children", []) + osgb_children
                    tile.update(root_tile_json)
                else:
                    tile["content"] = results[0].tile_json.get("content")
                    if osgb_children:
                        tile["children"] = osgb_children
            else:
                if osgb_children:
                    tile["children"] = osgb_children
        else:
            print()
            if osgb_children:
                tile["children"] = osgb_children

        return tile

    # ─────────────────────────────────────────────
    #  阶段一：四叉树重构辅助方法
    # ─────────────────────────────────────────────

    def _collect_leaf_tiles(self, tile: dict, output_dir: str) -> List[TileRecord]:
        """递归收集 tile 树中所有叶子瓦片为 TileRecord。"""
        from .metadata import bbox_center_lonlat

        tiles = []

        if "children" in tile:
            for child in tile["children"]:
                tiles.extend(self._collect_leaf_tiles(child, output_dir))
        elif "content" in tile:
            content_uri = tile["content"]["uri"]
            bv = tile.get("boundingVolume", {"sphere": [0, 0, 0, 1]})
            geo_error = tile.get("geometricError", 0)
            center = bbox_center_lonlat(bv)

            # 读取已写入的瓦片文件中的网格数据（用于后续合并）
            full_path = os.path.join(output_dir, content_uri)
            meshes = self._read_tile_meshes(full_path)

            tiles.append(TileRecord(
                name=os.path.basename(content_uri),
                bounding_volume=bv,
                geometric_error=geo_error,
                meshes=meshes,
                content_uri=content_uri,
                center_lonlat=center,
            ))

        return tiles

    def _read_tile_meshes(self, tile_path: str) -> List[OsgeMesh]:
        """从已写入的瓦片文件中回读网格数据。

        注意：这是一个轻量操作，仅在四叉树重构时使用。
        对于大文件可能有内存开销，但通常顶层瓦片数量有限。
        """
        if not os.path.exists(tile_path):
            return []

        try:
            with open(tile_path, "rb") as f:
                data = f.read()

            # 跳过 b3dm 头部（28 字节）如果存在
            if data[:4] == b"b3dm":
                # b3dm: 跳过头部 + feature table
                import struct
                ft_json_len = struct.unpack_from("<I", data, 12)[0]
                ft_bin_len = struct.unpack_from("<I", data, 16)[0]
                bt_json_len = struct.unpack_from("<I", data, 20)[0]
                bt_bin_len = struct.unpack_from("<I", data, 24)[0]
                glb_offset = 28 + ft_json_len + ft_bin_len + bt_json_len + bt_bin_len
                data = data[glb_offset:]

            # 简单回退：返回空列表，四叉树将仅使用包围盒信息
            # 完整的 GLB 解析过于复杂，这里仅做结构合并
            return []
        except Exception:
            return []

    # ─────────────────────────────────────────────
    #  辅助方法
    # ─────────────────────────────────────────────

    def _extract_lod_level(self, osgb_path: str) -> Optional[int]:
        """从文件名中提取 _Lxx_ 层级标识。"""
        return extract_level_from_filename(
            os.path.basename(osgb_path), self.structure_type, self._root_name_length
        )

    def _extract_lod_level_from_path(self, child_tile_path: str) -> Optional[int]:
        """从 PagedLOD 子路径中提取 _Lxx_ 层级标识。"""
        return extract_level_from_filename(
            os.path.basename(child_tile_path), self.structure_type, self._root_name_length
        )

    def _scan_level_range(self, node: OsgeTileNode) -> tuple:
        """预扫描瓦片树，提取基准层级和 range_max 一致性。

        Returns:
            (base_level, range_max_is_uniform)
        """
        levels = []
        range_max_values = []
        self._collect_scan_data(node, levels, range_max_values)

        base_level = min(levels) if levels else 0
        uniform = len(set(range_max_values)) <= 1 if range_max_values else True
        return base_level, uniform

    def _collect_scan_data(
        self, node: OsgeTileNode, levels: list, range_max_values: list
    ):
        """递归收集层级标识和 range_max 值。"""
        for child in node.children:
            self._collect_scan_data(child, levels, range_max_values)

        for pagelod in node.page_lods:
            range_max_values.append(pagelod.range_max)
            osgb_dir = ''  # 路径在预扫描时不可用，仅需文件名
            child_path = pagelod.child_tile_path
            level = extract_level_from_filename(
                os.path.basename(child_path), self.structure_type, self._root_name_length
            )
            if level is not None:
                levels.append(level)
            # 预扫描仅收集第一层子节点的 range_max 和 level
            # 不深入递归，避免重复加载

    def _load_mesh_textures(self, node: OsgeTileNode, osgb_dir: str):
        """为节点中的网格加载纹理数据。"""
        for mesh in node.meshes:
            if mesh.texture_path and mesh.texture_data is None:
                tex_path = os.path.join(osgb_dir, mesh.texture_path)
                if not os.path.isabs(mesh.texture_path):
                    if not os.path.exists(tex_path):
                        tex_path = mesh.texture_path
                mesh.texture_data = load_texture(tex_path)

    def _compute_bounding_volume(self, node: OsgeTileNode) -> dict:
        """计算节点的包围体。优先使用 OBB，回退到包围球。"""
        if node.meshes:
            return self._compute_bounding_volume_from_meshes(node.meshes)

        if node.page_lods:
            lod = node.page_lods[0]
            return {
                "sphere": [
                    float(lod.center[0]),
                    float(lod.center[1]),
                    float(lod.center[2]),
                    float(lod.radius),
                ]
            }

        return {"sphere": [0.0, 0.0, 0.0, 1.0]}

    def _compute_bounding_volume_from_meshes(self, meshes: List[OsgeMesh]) -> dict:
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

    def _compute_root_error(self, node: OsgeTileNode) -> float:
        """计算根节点的 geometricError。"""
        if node.page_lods:
            return max(
                pl.range_max * self.config.geometric_error_scale
                for pl in node.page_lods
            )
        if node.meshes:
            all_verts = np.concatenate([m.vertices for m in node.meshes if len(m.vertices) > 0])
            if len(all_verts) > 0:
                diag = np.linalg.norm(all_verts.max(axis=0) - all_verts.min(axis=0))
                return float(diag)
        return 1000.0

    def _unique_name(self, base_name: str) -> str:
        """生成唯一的瓦片文件名。"""
        stem = os.path.splitext(base_name)[0]
        return f"{stem}_{self.tile_counter:04d}{self._tile_extension}"

    @property
    def _tile_extension(self) -> str:
        """根据版本返回瓦片文件扩展名。"""
        return ".b3dm" if self.config.tiles_version == "1.0" else ".glb"



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
from .gltf_assembler import GlbAssembler, pack_glb
from .metadata import OsgeMetadata, local_to_ecef_transform
from .osgb_parser import OsgeTileNode, OsgeBinaryParser, OsgeMesh, PageLODInfo, compute_geometric_error
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

    def build(self, root_osgb: str, output_dir: str):
        """主入口：从根 OSGB 文件构建完整 3D Tiles 输出。"""
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(os.path.join(output_dir, "tiles"), exist_ok=True)

        if self.structure_type == StructureType.DJI_TERRA:
            self._root_name_length = len(os.path.splitext(os.path.basename(root_osgb))[0])

        print(f"  解析根文件: {os.path.basename(root_osgb)}")
        root_node = self.parser.parse_file(root_osgb)
        root_tile = self._process_node(root_node, root_osgb, output_dir, depth=0)

        root_error = self._compute_root_error(root_node)

        tileset = {
            "asset": {
                "version": "1.1",
                "generator": "OSGB2Tiles v1.0",
            },
            "geometricError": root_error,
            "root": root_tile,
        }

        tileset_path = os.path.join(output_dir, "tileset.json")
        with open(tileset_path, "w", encoding="utf-8") as f:
            json.dump(tileset, f, indent=2, ensure_ascii=False)

        return tileset_path

    # ─────────────────────────────────────────────
    #  核心流水线：LOD × 简化 × Draco 联动状态机
    # ─────────────────────────────────────────────

    def process_mesh_pipeline(
        self,
        meshes: List[OsgeMesh],
        tile_name: str,
        output_dir: str,
        parent_geometric_error: float,
    ) -> dict:
        """多参数联动核心调度函数。

        根据 enable_lod / enable_simplify / mesh_compression 的组合，
        决定生成单级或多级 GLB，并组装 3D Tiles 子树。

        联动矩阵：
        ┌─────────┬──────────────┬─────────┬────────────────────────────────┐
        │  LOD    │  simplify    │ draco   │  行为                          │
        ├─────────┼──────────────┼─────────┼────────────────────────────────┤
        │  ON     │  ON          │ ON/OFF  │ 情况A: 多级自适应简化           │
        │  ON     │  OFF         │ ON/OFF  │ 情况B: 多级结构，不简化          │
        │  OFF    │  ON          │ ON/OFF  │ 单级简化（无层级树）             │
        │  OFF    │  OFF         │ ON/OFF  │ 标准转换                       │
        └─────────┴──────────────┴─────────┴────────────────────────────────┘

        Draco 条件联动（情况C，仅 LOD 开启时生效）：
        - LOD0（最高质量）：不压缩，避免近景精度损失
        - LOD1/LOD2：应用 Draco 压缩，减小中远景瓦片体积

        Returns:
            3D Tiles tile dict（可能包含 children 子树）
        """
        if not meshes:
            return {}

        # ── 情况 A/B：LOD 开启 → 生成多级子树 ──
        if self.config.enable_lod:
            return self._build_lod_tree(
                meshes, tile_name, output_dir, parent_geometric_error
            )

        # ── 标准路径：无 LOD ──
        use_draco = self.config.mesh_compression
        glb_bytes = self._assemble_glb(meshes, tile_name, use_draco)

        self.tile_counter += 1
        glb_name = f"{tile_name}_{self.tile_counter:04d}.glb"
        glb_path = os.path.join("tiles", glb_name)

        full_path = os.path.join(output_dir, glb_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(glb_bytes)

        return {"content": {"uri": glb_path}}

    def _build_lod_tree(
        self,
        meshes: List[OsgeMesh],
        tile_name: str,
        output_dir: str,
        parent_geometric_error: float,
    ) -> dict:
        """构建 LOD 倒置树结构。

        3D Tiles 1.1 LOD 映射：
        - Root 节点 = LOD（最低细节，geometricError 最大，远景加载）
        - 叶子节点 = LOD0（最高细节，geometricError=0，近景加载）

        情况A（enable_simplify=True）：各层级网格经 meshoptimizer 简化
        情况B（enable_simplify=False）：各层级网格相同，仅结构分层
        """
        from .mesh_simplifier import generate_lod_meshes

        lod_levels = self.config.lod_levels  # [1.0, 0.5, 0.25] 高→低
        n_levels = len(lod_levels)

        # 为每个原始网格生成多级 LOD
        all_lod_meshes = []  # [lod_level_idx] = [SimplifyResult per mesh]
        for mesh in meshes:
            lod_results = generate_lod_meshes(
                mesh.vertices, mesh.normals, mesh.uvs, mesh.indices,
                lod_ratios=lod_levels,
                target_error=self.config.simplify_error,
            )
            # 继承纹理数据
            for result in lod_results:
                result.texture_data = mesh.texture_data
                result.texture_path = mesh.texture_path
            all_lod_meshes.append(lod_results)

        # 从最低精度到最高精度，构建倒置树
        # 最低精度(lod_levels[-1]) → Root，最高精度(lod_levels[0]) → Leaf
        tiles_by_level = []

        for level_idx in range(n_levels - 1, -1, -1):
            level_meshes = []
            for mesh_lods in all_lod_meshes:
                result = mesh_lods[level_idx]
                level_meshes.append(OsgeMesh(
                    vertices=result.vertices,
                    normals=result.normals,
                    uvs=result.uvs,
                    indices=result.indices,
                    texture_data=result.texture_data,
                    texture_path=result.texture_path,
                ))

            # 情况C：LOD0 不压缩，LOD1+ 压缩
            is_highest_detail = (level_idx == 0)
            if self.config.mesh_compression and self.config.enable_lod:
                use_draco = not is_highest_detail
            else:
                use_draco = self.config.mesh_compression

            # 生成 GLB
            glb_bytes = self._assemble_glb(level_meshes, tile_name, use_draco)

            self.tile_counter += 1
            suffix = f"lod{level_idx}"
            glb_name = f"{tile_name}_{suffix}_{self.tile_counter:04d}.glb"
            glb_path = os.path.join("tiles", glb_name)

            full_path = os.path.join(output_dir, glb_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(glb_bytes)

            # 计算几何误差
            if level_idx == 0:
                geo_error = 0.0  # LOD0（叶子）：近景加载，误差为 0
            else:
                # 按级别递减：级别越高（越粗糙）误差越大
                geo_error = parent_geometric_error * (1.0 / lod_levels[level_idx])

            tri_count = sum(len(m.indices) // 3 for m in level_meshes)
            draco_tag = "+Draco" if use_draco else ""
            print(f" → LOD{level_idx}: {glb_name} ({len(glb_bytes)/1024:.1f}KB, {tri_count}tris {draco_tag})")

            tiles_by_level.append({
                "geometricError": round(geo_error, 2),
                "refine": self.config.refine_mode.value,
                "boundingVolume": self._compute_bounding_volume_from_meshes(level_meshes),
                "content": {"uri": glb_path},
            })

        # 倒置树组装：Root → children → grandchildren
        # tiles_by_level[0] = LODn-1 (最粗糙) = Root
        # tiles_by_level[-1] = LOD0 (最精细) = Leaf
        root_tile = tiles_by_level[0]
        current = root_tile
        for i in range(1, len(tiles_by_level)):
            child = tiles_by_level[i]
            # 子节点继承 refine
            child["refine"] = self.config.refine_mode.value
            current["children"] = [child]
            current = child

        return root_tile

    # ─────────────────────────────────────────────
    #  GLB 组装（支持 Draco 条件控制）
    # ─────────────────────────────────────────────

    def _assemble_glb(
        self,
        meshes: List[OsgeMesh],
        tile_name: str,
        use_draco: bool,
    ) -> bytes:
        """组装 GLB 二进制，支持按需选择 Draco 压缩。"""
        # 临时覆盖 assembler 的压缩配置
        original_compression = self.assembler.config.mesh_compression
        self.assembler.config.mesh_compression = use_draco
        try:
            gltf_json, bin_data = self.assembler.build_gltf(meshes, tile_name=tile_name)
            return pack_glb(gltf_json, bin_data)
        finally:
            self.assembler.config.mesh_compression = original_compression

    # ─────────────────────────────────────────────
    #  标准节点处理（无 LOD 时的原有逻辑）
    # ─────────────────────────────────────────────

    def _process_node(
        self,
        node: OsgeTileNode,
        osgb_path: str,
        output_dir: str,
        depth: int,
    ) -> dict:
        """递归处理单个节点，生成 tile JSON + GLB 内容。"""
        tile = {}

        self.tile_counter += 1
        tile_name = os.path.basename(osgb_path)
        meshes_count = len(node.meshes)
        children_count = len(node.page_lods)
        print(f"  [{self.tile_counter}] 处理: {tile_name} (深度={depth}, 网格={meshes_count}, 子节点={children_count})", end="")
        sys.stdout.flush()

        # 1. 包围体
        tile["boundingVolume"] = self._compute_bounding_volume(node)

        # 2. 几何误差
        if node.page_lods:
            tile["geometricError"] = compute_geometric_error(
                node.page_lods[0], self.config.geometric_error_scale
            )
        else:
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

        # 6. 生成 GLB（LOD 或标准模式）
        if node.meshes:
            parent_error = tile["geometricError"]

            if self.config.enable_lod:
                # LOD 模式：使用 pipeline 生成多级子树
                lod_tile = self.process_mesh_pipeline(
                    node.meshes, node.name, output_dir, parent_error
                )
                # 将 LOD 子树的 content 或 children 合并到当前 tile
                if "children" in lod_tile:
                    tile["children"] = lod_tile["children"]
                if "content" in lod_tile:
                    tile["content"] = lod_tile["content"]

                # LOD 根节点使用原始 geometricError
                if "geometricError" in lod_tile:
                    tile["geometricError"] = lod_tile["geometricError"]
            else:
                # 标准模式
                glb_bytes = self._assemble_glb(
                    node.meshes, node.name, self.config.mesh_compression
                )
                glb_name = self._unique_name(node.name)
                glb_rel = os.path.join("tiles", glb_name)
                full_path = os.path.join(output_dir, glb_rel)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "wb") as f:
                    f.write(glb_bytes)
                tile["content"] = {"uri": glb_rel}
                print(f" → GLB: {glb_name} ({len(glb_bytes) / 1024:.1f}KB)")
        else:
            print()

        # 7. 递归子节点
        children = []
        for child in node.children:
            child_tile = self._process_node(child, osgb_path, output_dir, depth + 1)
            children.append(child_tile)

        for pagelod in node.page_lods:
            child_path = resolve_pagelod_path(
                osgb_dir, pagelod.child_tile_path, self.structure_type, osgb_path
            )
            if os.path.exists(child_path):
                child_node = self.parser.parse_file(child_path)
                child_tile = self._process_node(child_node, child_path, output_dir, depth + 1)
                children.append(child_tile)

        if children:
            # 如果已有 LOD children，合并
            existing = tile.get("children", [])
            tile["children"] = existing + children

        return tile

    # ─────────────────────────────────────────────
    #  辅助方法
    # ─────────────────────────────────────────────

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
        """生成唯一的 GLB 文件名。"""
        stem = os.path.splitext(base_name)[0]
        return f"{stem}_{self.tile_counter:04d}.glb"

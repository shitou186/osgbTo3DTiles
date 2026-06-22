"""3D Tiles 1.1 tileset.json 构建器。

递归遍历 OSGB 瓦片树，生成符合 3D Tiles 1.1 规范的 tileset.json 与 GLB 瓦片内容。
"""

import json
import os
import sys
import uuid
from typing import Optional

import numpy as np

from .config import ConvertConfig, RefineMode
from .gltf_assembler import GlbAssembler, pack_glb
from .metadata import OsgeMetadata, local_to_ecef_transform
from .osgb_parser import OsgeTileNode, OsgeBinaryParser, PageLODInfo, compute_geometric_error
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
        self._root_name_length = 0  # DJI Terra 根文件名长度，build() 中初始化

    def build(self, root_osgb: str, output_dir: str):
        """主入口：从根 OSGB 文件构建完整 3D Tiles 输出。"""
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(os.path.join(output_dir, "tiles"), exist_ok=True)

        # DJI Terra: 记录根文件名长度用于层级推算
        if self.structure_type == StructureType.DJI_TERRA:
            self._root_name_length = len(os.path.splitext(os.path.basename(root_osgb))[0])

        print(f"  解析根文件: {os.path.basename(root_osgb)}")
        root_node = self.parser.parse_file(root_osgb)
        root_tile = self._process_node(root_node, root_osgb, output_dir, depth=0)

        # 计算全局 geometricError
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

    def _process_node(
        self,
        node: OsgeTileNode,
        osgb_path: str,
        output_dir: str,
        depth: int,
    ) -> dict:
        """递归处理单个节点，生成 tile JSON + GLB 内容。"""
        tile = {}

        # 打印进度信息
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
            # 尝试从文件名推算层级
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

        # 6. 生成 GLB
        if node.meshes:
            glb_name = self._unique_name(node.name)
            glb_path = os.path.join("tiles", glb_name)

            gltf_json, bin_data = self.assembler.build_gltf(node.meshes, tile_name=node.name)
            glb_bytes = pack_glb(gltf_json, bin_data)

            full_path = os.path.join(output_dir, glb_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(glb_bytes)

            tile["content"] = {"uri": glb_path}
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
            tile["children"] = children

        return tile

    def _load_mesh_textures(self, node: OsgeTileNode, osgb_dir: str):
        """为节点中的网格加载纹理数据。"""
        for mesh in node.meshes:
            if mesh.texture_path and mesh.texture_data is None:
                # 尝试相对于 OSGB 文件目录解析纹理路径
                tex_path = os.path.join(osgb_dir, mesh.texture_path)
                if not os.path.isabs(mesh.texture_path):
                    # 也尝试相对于当前工作目录
                    if not os.path.exists(tex_path):
                        tex_path = mesh.texture_path
                mesh.texture_data = load_texture(tex_path)

    def _compute_bounding_volume(self, node: OsgeTileNode) -> dict:
        """计算节点的包围体。优先使用 OBB，回退到包围球。"""
        if node.meshes:
            all_verts = []
            for m in node.meshes:
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

    def _compute_root_error(self, node: OsgeTileNode) -> float:
        """计算根节点的 geometricError。"""
        if node.page_lods:
            return max(
                pl.range_max * self.config.geometric_error_scale
                for pl in node.page_lods
            )
        # 无 PageLOD 时根据包围体估算
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

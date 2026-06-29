"""OSGB 二进制格式解析器。

OSGB 文件结构（简化）：
- 文件头：魔数 "osg" + 版本号
- 对象序列化流：递归的 osg::Object 子类序列化数据

关键节点类型：
  osg::Group       → 子节点列表
  osg::Geode       → 几何体容器
  osg::Geometry    → 顶点/索引/法线/UV 数据
  osg::Texture2D   → 纹理引用或内嵌数据
  osg::PagedLOD    → 分页 LOD，含子文件引用与 RangeList

支持两种解析模式：
1. 标准 OSGB（ContextCapture）：直接解析二进制格式
2. DJI Terra OSGB：使用 osgconv 转换为 OBJ 格式后解析

依赖：DJI 格式需要 OpenSceneGraph (osgconv) 在 PATH 中。
"""

import os
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .config import ConvertConfig
from .obj_parser import ObjParser, compute_normals


@dataclass
class OsgeMesh:
    vertices: np.ndarray   # (N, 3) float32
    normals: np.ndarray    # (N, 3) float32
    uvs: np.ndarray        # (N, 2) float32
    indices: np.ndarray    # (M,) uint32
    texture_path: Optional[str] = None
    texture_data: Optional[bytes] = None
    lod_texture_size: Optional[int] = None  # LOD 级别对应的纹理尺寸


@dataclass
class PageLODInfo:
    range_min: float
    range_max: float
    child_tile_path: str
    center: Tuple[float, float, float]
    radius: float


@dataclass
class OsgeTileNode:
    name: str
    meshes: List[OsgeMesh] = field(default_factory=list)
    page_lods: List[PageLODInfo] = field(default_factory=list)
    children: List["OsgeTileNode"] = field(default_factory=list)
    local_bbox: tuple = (0, 0, 0, 0, 0, 0)


# ---- OSGB 对象类型常量 ----
OBJ_OBJECT = 0x01
OBJ_NODE = 0x02
OBJ_GROUP = 0x03
OBJ_GEODE = 0x04
OBJ_GEOMETRY = 0x10
OBJ_TEXTURE2D = 0x20
OBJ_PAGEDLOD = 0x30


class OsgeBinaryParser:
    """OSGB 二进制文件解析器。"""

    def __init__(self, config: ConvertConfig, swap_xy: bool = False):
        self.config = config
        self.swap_xy = swap_xy

    def parse_file(self, osgb_path: str) -> OsgeTileNode:
        """解析单个 OSGB 文件，返回瓦片节点树。

        支持两种头部格式：
        - 标准格式：osg 魔数在文件开头（偏移 0）
        - DJI Terra 封装格式：28 字节封装头 + 类名序列化流
        """
        with open(osgb_path, "rb") as f:
            data = f.read()

        is_dji, header_end = self._detect_format(data, osgb_path)

        if is_dji:
            root_node = self._parse_dji_file(data, header_end, osgb_path)
        else:
            version = struct.unpack_from("<I", data, 4)[0]
            root_node, _ = self._read_node(data, 8)
            root_node.name = os.path.basename(osgb_path)

        return root_node

    @staticmethod
    def _detect_format(data: bytes, osgb_path: str) -> tuple:
        """检测文件格式。返回 (is_dji, data_start_offset)。

        标准格式：前 3 字节是 'osg'
        DJI 格式：前 3 字节不是 'osg'，但在前 256 字节内能找到 'osg::' 类名
        """
        if data[:3] == b"osg":
            return False, 8

        # 检查是否是 DJI 格式：在前 256 字节内搜索 'osg::' 类名
        search_limit = min(256, len(data))
        pos = data.find(b"osg::", 0, search_limit)
        if pos > 0:
            return True, pos

        raise ValueError(f"无效的 OSGB 文件（魔数不匹配）: {osgb_path}")

    def _parse_dji_file(self, data: bytes, class_name_start: int, osgb_path: str) -> OsgeTileNode:
        """解析 DJI Terra 格式的 OSGB 文件。

        使用 osgconv 将 DJI OSGB 转换为 OBJ 格式，然后解析 OBJ 文件。
        如果 osgconv 不可用或转换失败，回退到提取子文件名引用。
        """
        node = OsgeTileNode(name=os.path.basename(osgb_path))

        # 提取类名
        class_end = data.find(b"\x00", class_name_start)
        if class_end < 0:
            class_end = class_name_start + 50
        class_name = data[class_name_start:class_end].decode("ascii", errors="replace")

        # 始终提取子文件名引用（用于 PagedLOD 树结构）
        child_names = self._extract_dji_child_names(data)
        osgb_dir = os.path.dirname(osgb_path)
        for child_name in child_names:
            child_path = os.path.join(osgb_dir, child_name)
            if os.path.exists(child_path):
                node.page_lods.append(
                    PageLODInfo(
                        range_min=0,
                        range_max=1000,
                        child_tile_path=child_name,
                        center=(0, 0, 0),
                        radius=100,
                    )
                )

        # 检查文件中是否包含几何数据（搜索所有 osg:: 类名）
        has_geometry = (
            b"osg::Geode" in data or
            b"osg::Geometry" in data or
            b"osg::Group" in data
        )
        if has_geometry:
            meshes = self._extract_dji_meshes_via_osgconv(osgb_path)
            if meshes:
                node.meshes = meshes

        return node

    def _extract_dji_meshes_via_osgconv(self, osgb_path: str) -> list:
        """使用 osgconv 将 DJI OSGB 转换为 OBJ 并提取几何数据。"""
        try:
            # 检查 osgconv 是否可用
            result = subprocess.run(
                ["which", "osgconv"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return []

            # 读取原始 OSGB 文件数据（用于提取嵌入纹理）
            with open(osgb_path, "rb") as f:
                osgb_data = f.read()

            # 提取嵌入的纹理
            embedded_textures = self._extract_dji_textures(osgb_data, osgb_path)

            # 转换为 OBJ
            with tempfile.TemporaryDirectory() as tmpdir:
                obj_path = os.path.join(tmpdir, "tile.obj")
                result = subprocess.run(
                    ["osgconv", osgb_path, obj_path],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    return []

                if not os.path.exists(obj_path):
                    return []

                # 解析 OBJ 文件
                obj_parser = ObjParser()
                obj_model = obj_parser.parse_file(obj_path)

                if not obj_model.meshes:
                    return []

                # 转换为 OsgeMesh
                meshes = []
                for obj_mesh in obj_model.meshes:
                    # osgconv 输出的 OBJ 法线可能是无效的统一值 (0,0,1)，需要重算
                    need_recompute = (
                        np.all(obj_mesh.normals == 0)
                        or len(obj_mesh.normals) == 0
                        or np.allclose(obj_mesh.normals, obj_mesh.normals[0], atol=1e-6)
                    )
                    if need_recompute and len(obj_mesh.indices) > 0:
                        obj_mesh.normals = compute_normals(obj_mesh.vertices, obj_mesh.indices)
                    elif need_recompute:
                        obj_mesh.normals = np.zeros_like(obj_mesh.vertices)

                    # glTF V 坐标原点在顶部，OBJ 在底部，需要翻转
                    uvs = obj_mesh.uvs.copy()
                    uvs[:, 1] = 1.0 - uvs[:, 1]

                    mesh = OsgeMesh(
                        vertices=obj_mesh.vertices,
                        normals=obj_mesh.normals,
                        uvs=uvs,
                        indices=obj_mesh.indices,
                    )

                    # 匹配嵌入纹理：材质名 material_N → 嵌入纹理索引 N-1
                    tex_idx = self._match_texture_index(obj_mesh.material_name)
                    if tex_idx is not None and tex_idx < len(embedded_textures):
                        mesh.texture_data = embedded_textures[tex_idx]
                    elif obj_mesh.texture_path:
                        mesh.texture_path = obj_mesh.texture_path

                    meshes.append(mesh)

                return meshes

        except Exception:
            return []

    @staticmethod
    def _match_texture_index(material_name: Optional[str]) -> Optional[int]:
        """从材质名提取纹理索引。material_N → N-1（0-based）。"""
        if not material_name:
            return None
        import re
        m = re.match(r"material_(\d+)", material_name)
        if m:
            return int(m.group(1)) - 1
        return None

    @staticmethod
    def _extract_dji_textures(data: bytes, osgb_path: str) -> list:
        """从 DJI OSGB 文件中提取嵌入的 JPEG 纹理。

        搜索 JPEG 文件头 (FF D8 FF) 和结束标记 (FF D9)。
        返回提取的纹理数据列表。
        """
        textures = []
        pos = 0
        while True:
            # 查找 JPEG 文件头
            pos = data.find(b'\xff\xd8\xff', pos)
            if pos == -1:
                break

            # 查找 JPEG 文件结束标记
            end_pos = data.find(b'\xff\xd9', pos + 3)
            if end_pos == -1:
                break

            # 提取 JPEG 数据
            jpeg_data = data[pos:end_pos + 2]
            if len(jpeg_data) > 100:  # 过滤太小的误匹配
                textures.append(jpeg_data)

            pos = end_pos + 2

        return textures

    @staticmethod
    def _extract_dji_child_names(data: bytes) -> list:
        """从 DJI OSGB 文件中提取子文件名引用。

        搜索两种格式：
        1. uint32长度 + 数字字符串.osgb（DJI Terra 格式）
        2. 路径形式的 .osgb 引用（ContextCapture 分幅格式，含相对路径 ../）
        """
        import re
        results = []

        # 格式1：纯数字文件名（DJI Terra）
        for m in re.finditer(rb"(\d{12,}\.osgb)", data):
            name = m.group().decode("ascii")
            pos = m.start()
            if pos >= 4:
                plen = struct.unpack_from("<I", data, pos - 4)[0]
                if plen == len(name) and name not in results:
                    results.append(name)

        # 格式2：路径形式的 .osgb 引用（ContextCapture 分幅）
        # 匹配如: ../Level_19/Tile_+000_+000.osgb, top/Level_18/Tile_xxx.osgb
        if not results:
            for m in re.finditer(rb"([\w/+._ -]+\.osgb)", data):
                name = m.group().decode("ascii", errors="replace")
                pos = m.start()
                if pos >= 4:
                    plen = struct.unpack_from("<I", data, pos - 4)[0]
                    if plen == len(name) and name not in results:
                        results.append(name)

        return results

    def _read_node(self, data: bytes, offset: int) -> Tuple[OsgeTileNode, int]:
        """递归读取 OSG 节点。返回 (节点, 新偏移量)。"""
        node = OsgeTileNode(name="")

        obj_type = struct.unpack_from("<I", data, offset)[0]
        offset += 4

        if obj_type == OBJ_GROUP:
            offset = self._read_group(data, offset, node)

        elif obj_type == OBJ_GEODE:
            offset = self._read_geode(data, offset, node)

        elif obj_type == OBJ_PAGEDLOD:
            offset = self._read_paged_lod(data, offset, node)

        return node, offset

    def _read_group(self, data: bytes, offset: int, node: OsgeTileNode) -> int:
        """解析 osg::Group：读取子节点数量并递归。"""
        num_children = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        for _ in range(num_children):
            child, offset = self._read_node(data, offset)
            node.children.append(child)
        return offset

    def _read_geode(self, data: bytes, offset: int, node: OsgeTileNode) -> int:
        """解析 osg::Geode：读取所有 Drawable（Geometry）。"""
        num_drawables = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        for _ in range(num_drawables):
            mesh, offset = self._read_geometry(data, offset)
            if mesh is not None:
                node.meshes.append(mesh)
        return offset

    def _read_paged_lod(self, data: bytes, offset: int, node: OsgeTileNode) -> int:
        """解析 osg::PagedLOD：读取 RangeList 与子文件引用。"""
        num_lods = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        for _ in range(num_lods):
            range_min = struct.unpack_from("<f", data, offset)[0]
            range_max = struct.unpack_from("<f", data, offset + 4)[0]
            offset += 8

            name_len = struct.unpack_from("<I", data, offset)[0]
            offset += 4
            child_path = data[offset : offset + name_len].decode("utf-8")
            offset += name_len

            cx, cy, cz, radius = struct.unpack_from("<4f", data, offset)
            offset += 16

            node.page_lods.append(
                PageLODInfo(
                    range_min=range_min,
                    range_max=range_max,
                    child_tile_path=child_path,
                    center=(cx, cy, cz),
                    radius=radius,
                )
            )
        return offset

    def _read_geometry(self, data: bytes, offset: int) -> Tuple[Optional[OsgeMesh], int]:
        """解析 osg::Geometry：提取顶点、索引、法线、UV 数据。"""
        mesh = OsgeMesh(
            vertices=np.array([], dtype=np.float32),
            normals=np.array([], dtype=np.float32),
            uvs=np.array([], dtype=np.float32),
            indices=np.array([], dtype=np.uint32),
        )

        # 顶点数组
        num_verts = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        if num_verts > 0:
            vert_data = np.frombuffer(
                data[offset : offset + num_verts * 12], dtype=np.float32
            )
            mesh.vertices = vert_data.reshape(-1, 3).copy()
            offset += num_verts * 12

        # 法线数组
        num_normals = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        if num_normals > 0:
            norm_data = np.frombuffer(
                data[offset : offset + num_normals * 12], dtype=np.float32
            )
            mesh.normals = norm_data.reshape(-1, 3).copy()
            offset += num_normals * 12

        # UV 数组
        num_uvs = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        if num_uvs > 0:
            uv_data = np.frombuffer(
                data[offset : offset + num_uvs * 8], dtype=np.float32
            )
            mesh.uvs = uv_data.reshape(-1, 2).copy()
            offset += num_uvs * 8

        # CRS 轴顺序修正：(Northing, Easting) → (Easting, Northing)
        if self.swap_xy:
            if len(mesh.vertices) > 0:
                mesh.vertices[:, [0, 1]] = mesh.vertices[:, [1, 0]]
            if len(mesh.normals) > 0:
                mesh.normals[:, [0, 1]] = mesh.normals[:, [1, 0]]

        # 索引数组（DrawElementsUInt）
        num_indices = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        if num_indices > 0:
            idx_data = np.frombuffer(
                data[offset : offset + num_indices * 4], dtype=np.uint32
            )
            mesh.indices = idx_data.copy()
            offset += num_indices * 4

        # 纹理引用
        has_texture = struct.unpack_from("<B", data, offset)[0]
        offset += 1
        if has_texture:
            tex_path_len = struct.unpack_from("<I", data, offset)[0]
            offset += 4
            mesh.texture_path = data[offset : offset + tex_path_len].decode("utf-8")
            offset += tex_path_len

        return mesh, offset


def compute_geometric_error(page_lod: PageLODInfo, scale: float = 1.0) -> float:
    """基于 PageLOD 的 RangeList 计算 geometricError。

    3D Tiles 中 geometricError 含义：
    - 值越大 = 越粗糙 = 优先渲染（高层级）
    - 值越小 = 越精细 = 远处裁剪

    OSGB PageLOD 的 range_max 对应"开始显示该层级的距离"，
    映射为：geometricError ≈ range_max * scale
    """
    error = page_lod.range_max * scale
    return max(error, 0.01)

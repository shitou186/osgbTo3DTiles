"""glTF 2.0 / GLB 组装器。

将 OSGB 网格数据转换为符合 glTF 2.0 规范的 GLB 二进制文件。
支持以下扩展：
- KHR_materials_unlit（无光照）
- KHR_texture_basisu（KTX2 纹理）
- EXT_texture_webp（WebP 纹理）
- KHR_draco_mesh_compression（Draco 网格压缩）
"""

import json
import struct
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import numpy as np

from .config import ConvertConfig, TextureFormat
from .osgb_parser import OsgeMesh
from .texture import encode_texture, get_mime_type, resize_texture


class GlbAssembler:
    """将 OSGB 网格数据组装为 glTF 2.0 / GLB 二进制。"""

    def __init__(self, config: ConvertConfig):
        self.config = config

    def build_gltf(
        self,
        meshes: List[OsgeMesh],
        tile_name: str = "tile",
    ) -> Tuple[dict, bytes]:
        """构建 glTF 2.0 JSON 结构与 BIN 数据。

        Returns:
            (gltf_json_dict, bin_bytes)
        """
        gltf = {
            "asset": {"version": "2.0", "generator": "OSGB2Tiles v1.0"},
            "extensionsUsed": [],
            "extensionsRequired": [],
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"name": tile_name, "mesh": 0}],
            "meshes": [],
            "accessors": [],
            "bufferViews": [],
            "buffers": [],
            "materials": [],
            "textures": [],
            "images": [],
            "samplers": [
                {
                    "magFilter": 9729,   # LINEAR
                    "minFilter": 9987,   # LINEAR_MIPMAP_LINEAR
                    "wrapS": 33071,      # CLAMP_TO_EDGE
                    "wrapT": 33071,
                }
            ],
        }

        bin_buffer = BytesIO()
        buffer_views: List[dict] = []
        accessors: List[dict] = []
        mesh_primitives: List[dict] = []

        # 为每个网格创建材质并处理纹理
        for mesh in meshes:
            material_idx = len(gltf["materials"])
            material = self._build_material()
            gltf["materials"].append(material)

            # 注册材质扩展
            for ext_name in material.get("extensions", {}):
                if ext_name not in gltf["extensionsUsed"]:
                    gltf["extensionsUsed"].append(ext_name)

            # 处理该网格的纹理
            if mesh.texture_data is not None:
                self._attach_texture(
                    mesh, material_idx, bin_buffer, buffer_views, gltf
                )

            primitive = self._process_mesh(
                mesh, bin_buffer, buffer_views, accessors, gltf
            )
            primitive["material"] = material_idx
            mesh_primitives.append(primitive)

        gltf["meshes"] = [{"primitives": mesh_primitives}]

        # 清理空扩展列表
        if not gltf["extensionsUsed"]:
            del gltf["extensionsUsed"]
        if not gltf["extensionsRequired"]:
            del gltf["extensionsRequired"]

        bin_data = bin_buffer.getvalue()
        gltf["buffers"] = [{"byteLength": len(bin_data)}]
        gltf["accessors"] = accessors
        gltf["bufferViews"] = buffer_views

        return gltf, bin_data

    def _process_mesh(
        self,
        mesh: OsgeMesh,
        bin_buffer: BytesIO,
        buffer_views: List[dict],
        accessors: List[dict],
        gltf: dict,
    ) -> dict:
        """处理单个网格，写入 buffer 并返回 primitive 描述。"""
        primitive: Dict = {"attributes": {}, "extensions": {}}

        if self.config.mesh_compression:
            return self._process_mesh_draco(mesh, bin_buffer, buffer_views, accessors, primitive, gltf)

        # ---- POSITION ----
        if len(mesh.vertices) > 0:
            bv_idx = self._write_buffer_view(
                bin_buffer, buffer_views, mesh.vertices.tobytes(), target=34962
            )
            acc_idx = self._write_accessor(
                accessors, bv_idx, len(mesh.vertices), "VEC3", 5126, mesh.vertices
            )
            primitive["attributes"]["POSITION"] = acc_idx

        # ---- NORMAL ----
        if len(mesh.normals) > 0:
            bv_idx = self._write_buffer_view(
                bin_buffer, buffer_views, mesh.normals.tobytes(), target=34962
            )
            acc_idx = self._write_accessor(
                accessors, bv_idx, len(mesh.normals), "VEC3", 5126
            )
            primitive["attributes"]["NORMAL"] = acc_idx

        # ---- TEXCOORD_0 ----
        if len(mesh.uvs) > 0:
            bv_idx = self._write_buffer_view(
                bin_buffer, buffer_views, mesh.uvs.tobytes(), target=34962
            )
            acc_idx = self._write_accessor(
                accessors, bv_idx, len(mesh.uvs), "VEC2", 5126
            )
            primitive["attributes"]["TEXCOORD_0"] = acc_idx

        # ---- INDICES ----
        if len(mesh.indices) > 0:
            # glTF 要求索引为无符号短整型或整型
            if mesh.indices.max() <= 65535:
                idx_data = mesh.indices.astype(np.uint16).tobytes()
                component_type = 5123  # UNSIGNED_SHORT
            else:
                idx_data = mesh.indices.astype(np.uint32).tobytes()
                component_type = 5125  # UNSIGNED_INT

            bv_idx = self._write_buffer_view(
                bin_buffer, buffer_views, idx_data, target=34963
            )
            acc_idx = self._write_accessor(
                accessors, bv_idx, len(mesh.indices), "SCALAR", component_type
            )
            primitive["indices"] = acc_idx

        if not primitive["extensions"]:
            del primitive["extensions"]

        return primitive

    def _process_mesh_draco(
        self,
        mesh: OsgeMesh,
        bin_buffer: BytesIO,
        buffer_views: List[dict],
        accessors: List[dict],
        primitive: dict,
        gltf: dict,
    ) -> dict:
        """使用 Draco 压缩网格数据。

        glTF 2.0 + KHR_draco_mesh_compression 规范要求：
        1. primitive.attributes 中必须保留 POSITION/NORMAL/TEXCOORD_0 的 accessor 索引
           （这些 accessor 需要包含 count、min、max，但不需要 bufferView）
        2. primitive.indices 必须指向一个有效的 accessor（同上）
        3. Draco 扩展的 attributes 映射属性 ID → decoder attribute ID
        """
        try:
            import DracoPy
        except ImportError:
            raise RuntimeError(
                "Draco 压缩需要 DracoPy 库。请安装：pip install DracoPy"
            )

        draco_data = DracoPy.encode(
            points=mesh.vertices.astype(np.float64) if len(mesh.vertices) > 0 else [],
            faces=mesh.indices.reshape(-1, 3).astype(np.int64) if len(mesh.indices) > 0 else None,
            normals=mesh.normals.astype(np.float64) if len(mesh.normals) > 0 else None,
            tex_coord=mesh.uvs.astype(np.float64) if len(mesh.uvs) > 0 else None,
            quantization_bits=11,
            compression_level=1,
        )

        bv_idx = self._write_buffer_view(
            bin_buffer, buffer_views, draco_data, target=None
        )

        # Draco 扩展引用
        primitive["extensions"]["KHR_draco_mesh_compression"] = {
            "bufferView": bv_idx,
            "attributes": {},
        }
        attr_map = primitive["extensions"]["KHR_draco_mesh_compression"]["attributes"]

        # 创建各属性的 accessor（Draco 要求原始 attributes 仍指向有效 accessor）
        # accessor 提供 count/min/max 信息，bufferView 设为 null（数据在 Draco 中）

        if len(mesh.vertices) > 0:
            acc_idx = len(accessors)
            accessors.append({
                "componentType": 5126,  # FLOAT
                "count": len(mesh.vertices),
                "type": "VEC3",
                "max": mesh.vertices.max(axis=0).tolist(),
                "min": mesh.vertices.min(axis=0).tolist(),
            })
            primitive["attributes"]["POSITION"] = acc_idx
            attr_map["POSITION"] = 0

        if len(mesh.normals) > 0:
            acc_idx = len(accessors)
            accessors.append({
                "componentType": 5126,
                "count": len(mesh.normals),
                "type": "VEC3",
            })
            primitive["attributes"]["NORMAL"] = acc_idx
            attr_map["NORMAL"] = 1

        if len(mesh.uvs) > 0:
            acc_idx = len(accessors)
            accessors.append({
                "componentType": 5126,
                "count": len(mesh.uvs),
                "type": "VEC2",
            })
            primitive["attributes"]["TEXCOORD_0"] = acc_idx
            attr_map["TEXCOORD_0"] = 2

        if len(mesh.indices) > 0:
            if mesh.indices.max() <= 65535:
                component_type = 5123  # UNSIGNED_SHORT
            else:
                component_type = 5125  # UNSIGNED_INT
            acc_idx = len(accessors)
            accessors.append({
                "componentType": component_type,
                "count": len(mesh.indices),
                "type": "SCALAR",
            })
            primitive["indices"] = acc_idx

        if not primitive["extensions"]:
            del primitive["extensions"]

        gltf["extensionsUsed"].append("KHR_draco_mesh_compression")
        gltf["extensionsRequired"].append("KHR_draco_mesh_compression")

        return primitive

    def _attach_texture(
        self,
        mesh: OsgeMesh,
        material_idx: int,
        bin_buffer: BytesIO,
        buffer_views: List[dict],
        gltf: dict,
    ):
        """将单个网格的纹理编码并关联到指定材质。"""
        raw = mesh.texture_data
        if raw is None:
            return

        # 如果原始数据已是目标格式且未超限，直接复用，避免重复编码损失质量
        if self.config.texture_format == TextureFormat.JPG and self._is_jpeg(raw):
            if self._jpeg_within_size(raw, self.config.max_texture_size):
                encoded = raw
            else:
                encoded = encode_texture(
                    resize_texture(raw, self.config.max_texture_size),
                    self.config.texture_format,
                )
        else:
            encoded = encode_texture(
                resize_texture(raw, self.config.max_texture_size),
                self.config.texture_format,
            )

        bv_idx = self._write_buffer_view(
            bin_buffer, buffer_views, encoded, target=None
        )
        img_index = len(gltf["images"])
        gltf["images"].append(
            {"bufferView": bv_idx, "mimeType": get_mime_type(self.config.texture_format)}
        )

        tex_index = len(gltf["textures"])
        gltf["textures"].append({"sampler": 0, "source": img_index})

        gltf["materials"][material_idx]["pbrMetallicRoughness"]["baseColorTexture"] = {
            "index": tex_index,
            "texCoord": 0,
        }

        if self.config.texture_format == TextureFormat.KTX2:
            if "KHR_texture_basisu" not in gltf["extensionsUsed"]:
                gltf["extensionsUsed"].append("KHR_texture_basisu")
                gltf["extensionsRequired"].append("KHR_texture_basisu")
            gltf["textures"][tex_index]["extensions"] = {
                "KHR_texture_basisu": {"source": img_index}
            }
        elif self.config.texture_format == TextureFormat.WEBP:
            if "EXT_texture_webp" not in gltf["extensionsUsed"]:
                gltf["extensionsUsed"].append("EXT_texture_webp")
            gltf["textures"][tex_index]["extensions"] = {
                "EXT_texture_webp": {"source": img_index}
            }

    @staticmethod
    def _is_jpeg(data: bytes) -> bool:
        """检查数据是否为 JPEG 格式（FF D8 FF 开头）。"""
        return len(data) >= 3 and data[:3] == b"\xff\xd8\xff"

    @staticmethod
    def _jpeg_within_size(data: bytes, max_size: int) -> bool:
        """粗略判断 JPEG 尺寸是否在限制内（读取 SOF0/SOF2 头部）。"""
        from PIL import Image
        import io
        try:
            img = Image.open(io.BytesIO(data))
            w, h = img.size
            return w <= max_size and h <= max_size
        except Exception:
            return False

    def _build_material(self) -> dict:
        """根据配置构建 glTF 材质。"""
        material = {
            "name": "OSGB_Material",
            "pbrMetallicRoughness": {
                "metallicFactor": 0.0,
                "roughnessFactor": 1.0,
            },
        }

        # 背面裁切 / 双面
        if self.config.force_double_sided:
            material["doubleSided"] = True
        else:
            material["doubleSided"] = not self.config.back_face_culling

        # 无光照
        if self.config.unlit_shading:
            material["extensions"] = {"KHR_materials_unlit": {}}

        return material

    @staticmethod
    def _write_buffer_view(
        bin_buffer: BytesIO,
        buffer_views: List[dict],
        data: bytes,
        target: Optional[int],
    ) -> int:
        """写入 buffer view，返回索引。"""
        # 4 字节对齐
        current = bin_buffer.tell()
        padding = (4 - current % 4) % 4
        if padding:
            bin_buffer.write(b"\x00" * padding)
            current += padding

        offset = current
        bin_buffer.write(data)

        bv = {
            "buffer": 0,
            "byteOffset": offset,
            "byteLength": len(data),
        }
        if target is not None:
            bv["target"] = target

        buffer_views.append(bv)
        return len(buffer_views) - 1

    @staticmethod
    def _write_accessor(
        accessors: List[dict],
        buffer_view_idx: int,
        count: int,
        type_: str,
        component_type: int,
        data: Optional[np.ndarray] = None,
    ) -> int:
        """写入 accessor，返回索引。"""
        acc = {
            "bufferView": buffer_view_idx,
            "componentType": component_type,
            "count": count,
            "type": type_,
        }
        if data is not None and type_ in ("VEC3", "VEC2"):
            acc["max"] = data.max(axis=0).tolist()
            acc["min"] = data.min(axis=0).tolist()

        accessors.append(acc)
        return len(accessors) - 1


def pack_glb(gltf_json: dict, bin_data: bytes) -> bytes:
    """将 glTF JSON + BIN 打包为 GLB 二进制格式。"""
    json_str = json.dumps(gltf_json, separators=(",", ":"), ensure_ascii=False)
    json_bytes = json_str.encode("utf-8")

    # JSON chunk 必须 4 字节对齐（空格补齐）
    while len(json_bytes) % 4 != 0:
        json_bytes += b" "

    # BIN chunk 必须 4 字节对齐（零补齐）
    bin_chunk = bin_data
    while len(bin_chunk) % 4 != 0:
        bin_chunk += b"\x00"

    total_length = 12 + 8 + len(json_bytes) + 8 + len(bin_chunk)

    buf = BytesIO()
    # GLB Header
    buf.write(struct.pack("<4sII", b"glTF", 2, total_length))
    # JSON Chunk
    buf.write(struct.pack("<I4s", len(json_bytes), b"JSON"))
    buf.write(json_bytes)
    # BIN Chunk
    buf.write(struct.pack("<I4s", len(bin_chunk), b"BIN\x00"))
    buf.write(bin_chunk)

    return buf.getvalue()

"""3D Tiles 1.0 b3dm 二进制打包模块。

将 glTF/GLB 数据封装为 Batched 3D Model (b3dm) 格式。
b3dm 是 3D Tiles 1.0 的标准瓦片格式，包含 28 字节头部 + Feature/Batch Table + glb 载荷。

规范参考：https://github.com/CesiumGS/3d-tiles/tree/main/specification/TileFormats/Batched3DModel
"""

import struct
from io import BytesIO


# b3dm 头部固定 28 字节
B3DM_HEADER_BYTE_LENGTH = 28

# b3dm 整体要求 8 字节对齐
B3DM_ALIGNMENT = 8


def package_to_b3dm(glb_bytes: bytes) -> bytes:
    """将 glb 二进制数据封装为 b3dm 格式。

    b3dm 文件结构：
    ┌──────────────────────────────────────────────┐
    │ Header (28 bytes)                            │
    │  - magic:        'b3dm'        (4 bytes)     │
    │  - version:      1             (4 bytes)     │
    │  - byteLength:   total length  (4 bytes)     │
    │  - featureTableJSONByteLength   (4 bytes)     │
    │  - featureTableBinaryByteLength (4 bytes)     │
    │  - batchTableJSONByteLength     (4 bytes)     │
    │  - batchTableBinaryByteLength   (4 bytes)     │
    ├──────────────────────────────────────────────┤
    │ Feature Table JSON (padded to 8-byte align)  │
    ├──────────────────────────────────────────────┤
    │ glb payload (padded to 8-byte align)         │
    └──────────────────────────────────────────────┘

    Args:
        glb_bytes: 完整的 glTF 2.0 GLB 二进制数据

    Returns:
        完整的 b3dm 二进制数据
    """
    # Feature Table：最小合法 JSON（含 BATCH_LENGTH）
    feature_table_json = b'{"BATCH_LENGTH":0}'
    # 补齐到 8 字节对齐
    ft_padding = (B3DM_ALIGNMENT - len(feature_table_json) % B3DM_ALIGNMENT) % B3DM_ALIGNMENT
    feature_table_json += b" " * ft_padding

    feature_table_binary = b""
    batch_table_json = b""
    batch_table_binary = b""

    # glb 载荷也需要 8 字节对齐
    glb_padding = (B3DM_ALIGNMENT - len(glb_bytes) % B3DM_ALIGNMENT) % B3DM_ALIGNMENT
    glb_padded = glb_bytes + b"\x00" * glb_padding

    # 计算总长度并确保 8 字节对齐
    total_length = (
        B3DM_HEADER_BYTE_LENGTH
        + len(feature_table_json)
        + len(feature_table_binary)
        + len(batch_table_json)
        + len(batch_table_binary)
        + len(glb_padded)
    )
    # b3dm 规范要求整体 8 字节对齐
    total_padding = (B3DM_ALIGNMENT - total_length % B3DM_ALIGNMENT) % B3DM_ALIGNMENT
    total_length += total_padding

    buf = BytesIO()

    # ── Header (28 bytes) ──
    buf.write(b"b3dm")                                          # magic
    buf.write(struct.pack("<I", 1))                             # version
    buf.write(struct.pack("<I", total_length))                  # byteLength
    buf.write(struct.pack("<I", len(feature_table_json)))       # featureTableJSONByteLength
    buf.write(struct.pack("<I", len(feature_table_binary)))     # featureTableBinaryByteLength
    buf.write(struct.pack("<I", len(batch_table_json)))         # batchTableJSONByteLength
    buf.write(struct.pack("<I", len(batch_table_binary)))       # batchTableBinaryByteLength

    # ── Feature Table JSON ──
    buf.write(feature_table_json)

    # ── Feature Table Binary ──
    buf.write(feature_table_binary)

    # ── Batch Table JSON ──
    buf.write(batch_table_json)

    # ── Batch Table Binary ──
    buf.write(batch_table_binary)

    # ── glb payload ──
    buf.write(glb_padded)

    # ── 总体对齐填充 ──
    if total_padding > 0:
        buf.write(b"\x00" * total_padding)

    return buf.getvalue()


def validate_b3dm(data: bytes) -> bool:
    """校验 b3dm 二进制数据的合法性。

    检查项：
    1. Magic = 'b3dm'
    2. Version = 1
    3. ByteLength 与实际长度一致
    4. 总长度 8 字节对齐
    """
    if len(data) < B3DM_HEADER_BYTE_LENGTH:
        return False

    magic = data[:4]
    if magic != b"b3dm":
        return False

    version = struct.unpack_from("<I", data, 4)[0]
    if version != 1:
        return False

    byte_length = struct.unpack_from("<I", data, 8)[0]
    if byte_length != len(data):
        return False

    if byte_length % B3DM_ALIGNMENT != 0:
        return False

    return True

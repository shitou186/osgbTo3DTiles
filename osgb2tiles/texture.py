"""纹理处理模块：格式转换与尺寸控制。"""

import io
from typing import Optional

from PIL import Image

from .config import TextureFormat


def load_texture(texture_path: str) -> Optional[bytes]:
    """从文件路径加载纹理，返回原始图像字节。"""
    try:
        with open(texture_path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def resize_texture(image_data: bytes, max_size: int) -> bytes:
    """将纹理缩放到不超过 max_size 的最大 2 的幂次尺寸。"""
    img = Image.open(io.BytesIO(image_data))
    w, h = img.size

    if w <= max_size and h <= max_size:
        return image_data

    def next_power_of_2(v: int) -> int:
        p = 1
        while p < v:
            p <<= 1
        return min(p, max_size)

    new_w = next_power_of_2(w)
    new_h = next_power_of_2(h)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def encode_texture(
    image_data: bytes,
    fmt: TextureFormat,
    quality: int = 85,
) -> bytes:
    """根据目标格式编码纹理。

    Args:
        image_data: 原始图像字节（PNG/BMP 等无格式）
        fmt: 目标格式
        quality: JPEG/WebP 质量 (1-100)

    Returns:
        编码后的字节
    """
    img = Image.open(io.BytesIO(image_data))

    if img.mode == "RGBA" and fmt == TextureFormat.JPG:
        img = img.convert("RGB")

    buf = io.BytesIO()

    if fmt == TextureFormat.JPG:
        img.save(buf, format="JPEG", quality=quality, optimize=True)
    elif fmt == TextureFormat.WEBP:
        img.save(buf, format="WEBP", quality=quality, method=4)
    elif fmt == TextureFormat.KTX2:
        return _encode_ktx2(img)
    else:
        img.save(buf, format="PNG")

    return buf.getvalue()


def _encode_ktx2(img: Image.Image) -> bytes:
    """将图像编码为 KTX2/Basis Universal 格式。

    调用 toktx 命令行工具完成转码，使用 tempfile 管理中间文件。
    """
    import subprocess
    import tempfile
    import os

    tmp_in_path = None
    tmp_out_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_in:
            img.save(tmp_in, format="PNG")
            tmp_in_path = tmp_in.name

        tmp_out_path = tmp_in_path.replace(".png", ".ktx2")

        cmd = [
            "toktx",
            "--t2",                   # 输出 KTX2
            "--bcmp",                 # Basis Universal 压缩
            "--clevel", "2",          # 压缩级别
            "--qlevel", "128",        # 质量
            tmp_out_path,
            tmp_in_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        with open(tmp_out_path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        raise RuntimeError(
            "未找到 toktx 工具。请安装 KTX-Software：https://github.com/KhronosGroup/KTX-Software"
        )
    finally:
        if tmp_in_path and os.path.exists(tmp_in_path):
            os.unlink(tmp_in_path)
        if tmp_out_path and os.path.exists(tmp_out_path):
            os.unlink(tmp_out_path)


def get_mime_type(fmt: TextureFormat) -> str:
    return {
        TextureFormat.JPG: "image/jpeg",
        TextureFormat.WEBP: "image/webp",
        TextureFormat.KTX2: "image/ktx2",
    }[fmt]


def encode_texture_batch(
    items: list,
    max_workers: int = 4,
) -> list:
    """批量编码纹理，支持 ProcessPoolExecutor 并行。

    Args:
        items: [(image_data, format, quality), ...] 元组列表
        max_workers: 最大并行工作进程数

    Returns:
        编码后的字节列表
    """
    if len(items) <= 1:
        return [encode_texture(data, fmt, q) for data, fmt, q in items]

    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(encode_texture, data, fmt, q)
            for data, fmt, q in items
        ]
        return [f.result() for f in futures]

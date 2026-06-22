"""OSGB → 3D Tiles 1.1 转换工具 CLI 入口。"""

import argparse
import os
import sys
import time

from .config import ConvertConfig, RefineMode, TextureFormat
from .metadata import parse_metadata
from .structure import StructureType, detect_structure, find_root_osgb as _find_root
from .tileset_builder import TilesetBuilder


def find_metadata(input_dir: str) -> str:
    """在输入目录中查找 metadata.xml。"""
    candidates = [
        os.path.join(input_dir, "metadata.xml"),
        os.path.join(input_dir, "Metadata.xml"),
        os.path.join(input_dir, "MetaData.xml"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return ""


def find_root_osgb(input_dir: str) -> tuple:
    """查找根 OSGB 文件，同时返回检测到的结构类型。

    Returns:
        (root_osgb_path, structure_type) 元组
    """
    structure_type = detect_structure(input_dir)
    root_path = _find_root(input_dir, structure_type)
    return root_path, structure_type


def main():
    parser = argparse.ArgumentParser(
        description="OSGB 倾斜摄影数据 → 3D Tiles 1.1 转换工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  python -m osgb2tiles -i ./Data -o ./output
  python -m osgb2tiles -i ./Data -o ./output --texture webp --no-unlit
  python -m osgb2tiles -i ./Data -o ./output --draco --texture ktx2 --double-sided
""",
    )

    parser.add_argument("-i", "--input", required=True, help="OSGB 数据目录路径")
    parser.add_argument("-o", "--output", required=True, help="输出目录路径")

    # 可视化参数
    parser.add_argument(
        "--back-face-culling",
        action="store_true",
        default=True,
        help="启用背面裁切（默认开启）",
    )
    parser.add_argument(
        "--no-back-face-culling",
        action="store_false",
        dest="back_face_culling",
        help="禁用背面裁切",
    )
    parser.add_argument(
        "--double-sided",
        action="store_true",
        default=False,
        help="强制双面渲染（与背面裁切互斥）",
    )
    parser.add_argument(
        "--unlit",
        action="store_true",
        default=True,
        help="启用无光照/Unlit 着色（默认开启）",
    )
    parser.add_argument(
        "--no-unlit",
        action="store_false",
        dest="unlit",
        help="禁用无光照，使用标准 PBR 材质",
    )

    # 纹理参数
    parser.add_argument(
        "--texture",
        choices=["jpg", "webp", "ktx2"],
        default="jpg",
        help="输出纹理格式（默认 jpg）",
    )
    parser.add_argument(
        "--max-texture-size",
        type=int,
        default=2048,
        help="纹理最大尺寸（默认 2048）",
    )

    # 网格压缩
    parser.add_argument(
        "--draco",
        action="store_true",
        default=False,
        help="启用 Draco 网格压缩",
    )

    # 切片参数
    parser.add_argument(
        "--refine",
        choices=["ADD", "REPLACE"],
        default="REPLACE",
        help="细化模式（默认 REPLACE）",
    )
    parser.add_argument(
        "--error-scale",
        type=float,
        default=1.0,
        help="几何误差缩放因子（默认 1.0）",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=8,
        help="并行线程数（默认 8）",
    )

    args = parser.parse_args()

    # 构建配置
    config = ConvertConfig(
        back_face_culling=args.back_face_culling,
        force_double_sided=args.double_sided,
        unlit_shading=args.unlit,
        texture_format=TextureFormat(args.texture),
        mesh_compression=args.draco,
        refine_mode=RefineMode(args.refine),
        geometric_error_scale=args.error_scale,
        max_texture_size=args.max_texture_size,
        threads=args.threads,
        ecef_transform=True,
    )

    try:
        config.validate()
    except ValueError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        sys.exit(1)

    input_dir = os.path.abspath(args.input)
    output_dir = os.path.abspath(args.output)

    if not os.path.isdir(input_dir):
        print(f"错误: 输入目录不存在: {input_dir}", file=sys.stderr)
        sys.exit(1)

    # 解析元数据
    metadata_path = find_metadata(input_dir)
    if metadata_path:
        print(f"[1/4] 解析元数据: {metadata_path}")
        metadata = parse_metadata(metadata_path)
        print(f"       坐标系: {metadata.srs}")
        print(f"       原点: ({metadata.origin_lon:.6f}, {metadata.origin_lat:.6f}, {metadata.origin_height:.2f})")
    else:
        print("[警告] 未找到 metadata.xml，使用默认元数据")
        from .metadata import OsgeMetadata
        metadata = OsgeMetadata(
            origin_lon=116.0, origin_lat=39.0, origin_height=0.0,
            srs="EPSG:4326", bounding_box=(0, 0, 0, 0, 0, 0),
        )

    # 查找根 OSGB
    print("[2/4] 查找根 OSGB 文件...")
    root_osgb, structure_type = find_root_osgb(input_dir)
    print(f"       根文件: {root_osgb}")
    print(f"       结构类型: {structure_type.value}")

    # 执行转换
    print("[3/4] 开始转换...")
    start_time = time.time()

    builder = TilesetBuilder(config, metadata, structure_type=structure_type)
    tileset_path = builder.build(root_osgb, output_dir)

    elapsed = time.time() - start_time
    print(f"[4/4] 转换完成!")
    print(f"       输出: {tileset_path}")
    print(f"       耗时: {elapsed:.2f}s")
    print(f"       生成瓦片数: {builder.tile_counter}")


if __name__ == "__main__":
    main()

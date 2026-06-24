"""多工程 3D Tiles 索引合并工具（阶段二）。

纯文本级操作，不加载任何网格/纹理数据，秒级完成。
将多个独立 3D Tiles 工程合并为一个统一的 tileset.json，
使 Cesium 可通过单一入口加载所有区域。

使用方式：
    python -m osgb2tiles merge -i ./project_A ./project_B -o ./merged/tileset.json
"""

import argparse
import json
import os
import sys
from typing import List, Optional, Tuple


def merge_tilesets(input_dirs: List[str], output_file: str):
    """合并多个 3D Tiles 工程的 tileset.json 为统一入口。

    Args:
        input_dirs: 子工程目录列表（每个目录含 tileset.json）
        output_file: 输出的总览 tileset.json 路径
    """
    children = []
    global_bbox = None
    global_sphere = None

    for proj_dir in input_dirs:
        tileset_path = os.path.join(proj_dir, "tileset.json")
        if not os.path.exists(tileset_path):
            print(f"  [跳过] 未找到 tileset.json: {proj_dir}")
            continue

        with open(tileset_path, "r", encoding="utf-8") as f:
            tileset = json.load(f)

        root = tileset.get("root", {})
        bbox = root.get("boundingVolume", {})
        geo_error = root.get("geometricError", 1000.0)

        # 计算相对路径
        output_dir = os.path.dirname(os.path.abspath(output_file))
        rel_path = os.path.relpath(tileset_path, output_dir)

        child_entry = {
            "boundingVolume": bbox,
            "geometricError": geo_error,
            "refine": root.get("refine", "REPLACE"),
            "content": {"uri": rel_path},
        }

        # 继承 transform（如果有）
        if "transform" in root:
            child_entry["transform"] = root["transform"]

        children.append(child_entry)

        # 合并全局包围盒
        global_bbox = _merge_bounding_volumes(global_bbox, bbox)

        proj_name = os.path.basename(os.path.normpath(proj_dir))
        print(f"  [已收录] {proj_name} (误差={geo_error:.1f})")

    if not children:
        print("错误: 没有有效的子工程可合并", file=sys.stderr)
        sys.exit(1)

    # 计算全局 geometricError（所有子工程中最大值 * 2）
    max_error = max(c["geometricError"] for c in children)

    merged_tileset = {
        "asset": {
            "version": "1.1",
            "generator": "OSGB2Tiles MergeTool v1.0",
        },
        "geometricError": max_error * 2.0,
        "root": {
            "boundingVolume": global_bbox,
            "geometricError": max_error * 2.0,
            "refine": "REPLACE",
            "children": children,
        },
    }

    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(merged_tileset, f, indent=2, ensure_ascii=False)

    print(f"\n合并完成: {len(children)} 个子工程 → {output_file}")


def _merge_bounding_volumes(existing: Optional[dict], new: dict) -> dict:
    """将新的包围盒合并到全局包围盒中。

    支持两种 3D Tiles 包围盒格式：
    - box: [cx, cy, cz, hx, 0, 0, 0, hy, 0, 0, 0, hz] (OBB)
    - sphere: [cx, cy, cz, radius]

    合并策略：取两者的最小外接包围盒。
    """
    if existing is None:
        return new

    existing_min, existing_max = _bbox_to_minmax(existing)
    new_min, new_max = _bbox_to_minmax(new)

    merged_min = [min(a, b) for a, b in zip(existing_min, new_min)]
    merged_max = [max(a, b) for a, b in zip(existing_max, new_max)]

    return _minmax_to_box(merged_min, merged_max)


def _bbox_to_minmax(bbox: dict) -> Tuple[List[float], List[float]]:
    """将 3D Tiles boundingVolume 转为 (min_xyz, max_xyz)。"""
    if "box" in bbox:
        box = bbox["box"]
        cx, cy, cz = box[0], box[1], box[2]
        hx, hy, hz = box[3], box[7], box[11]
        return [cx - hx, cy - hy, cz - hz], [cx + hx, cy + hy, cz + hz]
    elif "sphere" in bbox:
        s = bbox["sphere"]
        cx, cy, cz, r = s[0], s[1], s[2], s[3]
        return [cx - r, cy - r, cz - r], [cx + r, cy + r, cz + r]
    return [0, 0, 0], [0, 0, 0]


def _minmax_to_box(min_xyz: list, max_xyz: list) -> dict:
    """将 (min_xyz, max_xyz) 转为 3D Tiles box 格式。"""
    cx = (min_xyz[0] + max_xyz[0]) / 2
    cy = (min_xyz[1] + max_xyz[1]) / 2
    cz = (min_xyz[2] + max_xyz[2]) / 2
    hx = (max_xyz[0] - min_xyz[0]) / 2
    hy = (max_xyz[1] - min_xyz[1]) / 2
    hz = (max_xyz[2] - min_xyz[2]) / 2
    return {
        "box": [cx, cy, cz, hx, 0.0, 0.0, 0.0, hy, 0.0, 0.0, 0.0, hz]
    }


def main():
    """merge 子命令入口。"""
    parser = argparse.ArgumentParser(
        description="多工程 3D Tiles 索引合并工具（纯文本级，秒级完成）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  python -m osgb2tiles merge -i ./region_A ./region_B -o ./merged/tileset.json
  python -m osgb2tiles merge -i ./proj1 ./proj2 ./proj3 -o ./all/tileset.json
""",
    )

    parser.add_argument(
        "-i", "--input",
        nargs="+",
        required=True,
        help="子工程目录列表（每个目录需含 tileset.json）",
    )
    parser.add_argument(
        "-o", "--output",
        required=True,
        help="输出的总览 tileset.json 路径",
    )

    args = parser.parse_args()

    # 验证输入目录
    valid_dirs = []
    for d in args.input:
        abs_d = os.path.abspath(d)
        if not os.path.isdir(abs_d):
            print(f"警告: 目录不存在，跳过: {abs_d}", file=sys.stderr)
            continue
        valid_dirs.append(abs_d)

    if not valid_dirs:
        print("错误: 没有有效的输入目录", file=sys.stderr)
        sys.exit(1)

    print(f"[合并工具] 收录 {len(valid_dirs)} 个子工程...")
    merge_tilesets(valid_dirs, os.path.abspath(args.output))

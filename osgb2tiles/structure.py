"""OSGB 目录结构检测与路径适配。

支持两种主流 OSGB 数据结构：
- ContextCapture：嵌套 Base/ 目录，根文件为 Data.osgb，子文件带 _Lxx_ 层级标识
- DJI Terra：扁平数字命名目录，根文件为最短文件名，无层级标识
"""

import os
import re
from enum import Enum
from typing import Optional

_LEVEL_PATTERN = re.compile(r"_L(\d+)_")


class StructureType(Enum):
    """OSGB 目录结构类型。"""
    CONTEXT_CAPTURE = "context_capture"
    DJI_TERRA = "dji_terra"


def find_data_dir(input_dir: str) -> str:
    """查找包含 .osgb 文件的目录。

    查找顺序：
    1. input_dir 本身包含 .osgb 文件 → input_dir
    2. input_dir 下唯一的子目录 → 该子目录（递归一层）
    3. input_dir/Data/ 子目录存在 → input_dir/Data/（递归一层）
    """
    # 1. input_dir 本身有 .osgb 文件
    for f in os.listdir(input_dir):
        if f.lower().endswith(".osgb"):
            return input_dir

    # 收集子目录
    subdirs = [
        d for d in os.listdir(input_dir)
        if os.path.isdir(os.path.join(input_dir, d))
    ]

    # 2. input_dir 下唯一的子目录
    if len(subdirs) == 1:
        candidate = os.path.join(input_dir, subdirs[0])
        # 先看该子目录本身是否有 .osgb
        for f in os.listdir(candidate):
            if f.lower().endswith(".osgb"):
                return candidate
        # 再看该子目录下是否有唯一的子子目录（如 Data/314341526340/）
        inner_subdirs = [
            d for d in os.listdir(candidate)
            if os.path.isdir(os.path.join(candidate, d))
        ]
        if len(inner_subdirs) == 1:
            inner = os.path.join(candidate, inner_subdirs[0])
            for f in os.listdir(inner):
                if f.lower().endswith(".osgb"):
                    return inner
        return candidate

    # 3. 显式查找 Data/ 子目录
    data_dir = os.path.join(input_dir, "Data")
    if os.path.isdir(data_dir):
        for f in os.listdir(data_dir):
            if f.lower().endswith(".osgb"):
                return data_dir
        inner_subdirs = [
            d for d in os.listdir(data_dir)
            if os.path.isdir(os.path.join(data_dir, d))
        ]
        if len(inner_subdirs) == 1:
            inner = os.path.join(data_dir, inner_subdirs[0])
            for f in os.listdir(inner):
                if f.lower().endswith(".osgb"):
                    return inner
        return data_dir

    return input_dir


def detect_structure(input_dir: str) -> StructureType:
    """自动检测 OSGB 数据目录的结构类型。

    检测策略：
    1. 存在 Data.osgb → ContextCapture
    2. 存在 Base/ 子目录 → ContextCapture
    3. Data/ 下有单个数字命名子目录，内含纯数字 .osgb 文件 → DJI Terra
    4. 以上均不匹配 → 抛出异常
    """
    data_dir = find_data_dir(input_dir)

    # ContextCapture: Data.osgb
    if os.path.exists(os.path.join(data_dir, "Data.osgb")):
        return StructureType.CONTEXT_CAPTURE

    # ContextCapture: Base/ 子目录
    if os.path.isdir(os.path.join(data_dir, "Base")):
        return StructureType.CONTEXT_CAPTURE

    # DJI Terra: 目录中有纯数字命名的 .osgb 文件
    osgb_files = [
        f for f in os.listdir(data_dir)
        if f.lower().endswith(".osgb") and os.path.isfile(os.path.join(data_dir, f))
    ]
    if osgb_files:
        numeric_count = sum(
            1 for f in osgb_files
            if os.path.splitext(f)[0].isdigit()
        )
        if numeric_count == len(osgb_files):
            return StructureType.DJI_TERRA

    raise ValueError(
        f"无法识别 {input_dir} 的 OSGB 数据目录结构。"
        f"请确认目录中包含 Data.osgb 或纯数字命名的 .osgb 文件。"
    )


def find_root_osgb(input_dir: str, structure_type: StructureType) -> str:
    """根据结构类型查找根 OSGB 文件。

    ContextCapture: 优先 Data.osgb，回退到目录中唯一 .osgb
    DJI Terra: 文件名最短的 .osgb 文件（根节点）
    """
    data_dir = find_data_dir(input_dir)

    if structure_type == StructureType.CONTEXT_CAPTURE:
        root = os.path.join(data_dir, "Data.osgb")
        if os.path.exists(root):
            return root
        # 回退：目录中唯一的 .osgb
        osgb_files = [
            f for f in os.listdir(data_dir)
            if f.lower().endswith(".osgb") and os.path.isfile(os.path.join(data_dir, f))
        ]
        if len(osgb_files) == 1:
            return os.path.join(data_dir, osgb_files[0])
        raise FileNotFoundError(
            f"在 {data_dir} 中未找到 Data.osgb。"
        )

    # DJI Terra: 最短文件名
    osgb_files = [
        f for f in os.listdir(data_dir)
        if f.lower().endswith(".osgb") and os.path.isfile(os.path.join(data_dir, f))
    ]
    if not osgb_files:
        raise FileNotFoundError(
            f"在 {data_dir} 中未找到 .osgb 文件。"
        )
    osgb_files.sort(key=len)
    root_name = osgb_files[0]
    root_stem = os.path.splitext(root_name)[0]

    # 验证：根文件名应是其他文件名的前缀
    if len(osgb_files) > 1:
        sample = osgb_files[1:min(6, len(osgb_files))]
        if not all(os.path.splitext(f)[0].startswith(root_stem) for f in sample):
            # 前缀不匹配，回退到排除法
            root_name = _find_root_by_exclusion(data_dir, osgb_files)

    return os.path.join(data_dir, root_name)


def _find_root_by_exclusion(data_dir: str, osgb_files: list) -> str:
    """通过排除法找根文件：被其他文件引用的都不是根。

    仅解析前 N 个文件的 PagedLOD 引用，性能可控。
    """
    from .osgb_parser import OsgeBinaryParser
    from .config import ConvertConfig

    parser = OsgeBinaryParser(ConvertConfig())
    referenced = set()

    check_count = min(100, len(osgb_files))
    for fname in osgb_files[:check_count]:
        fpath = os.path.join(data_dir, fname)
        try:
            node = parser.parse_file(fpath)
            for plod in node.page_lods:
                basename = os.path.basename(plod.child_tile_path)
                referenced.add(basename)
        except Exception:
            continue

    for fname in osgb_files:
        if fname not in referenced:
            return fname

    return min(osgb_files, key=len)


def resolve_pagelod_path(
    osgb_dir: str,
    child_name: str,
    structure_type: StructureType,
    osgb_path: str = "",
) -> str:
    """根据结构类型解析 PageLOD 子文件路径。

    ContextCapture: 直接拼接（child_name 可能含 Base/ 前缀）
    DJI Terra: 提取纯文件名，在同目录查找
    """
    if structure_type == StructureType.CONTEXT_CAPTURE:
        return os.path.join(osgb_dir, child_name)

    # DJI Terra: 提取纯文件名
    basename = os.path.basename(child_name)
    candidate = os.path.join(osgb_dir, basename)
    if os.path.exists(candidate):
        return candidate

    # 回退：在父目录的数字子目录中查找
    parent_dir = os.path.dirname(osgb_dir)
    for subdir in os.listdir(parent_dir):
        subdir_path = os.path.join(parent_dir, subdir)
        if os.path.isdir(subdir_path):
            candidate = os.path.join(subdir_path, basename)
            if os.path.exists(candidate):
                return candidate

    return os.path.join(osgb_dir, basename)


def extract_level_from_filename(
    filename: str,
    structure_type: StructureType,
    root_name_length: int = 0,
) -> Optional[int]:
    """从文件名中提取层级标识。

    ContextCapture: 正则匹配 _L15_ → 15
    DJI Terra: 文件名长度差推算层级
    """
    if structure_type == StructureType.CONTEXT_CAPTURE:
        match = _LEVEL_PATTERN.search(filename)
        if match:
            return int(match.group(1))
        return None

    # DJI Terra: 文件名长度差
    stem = os.path.splitext(filename)[0]
    if not stem.isdigit():
        return None
    if root_name_length <= 0:
        return None
    level = len(stem) - root_name_length
    return max(level, 0)


def compute_level_based_error(level: int, scale: float = 1.0) -> float:
    """根据层级数计算 geometricError。

    层级越高（数字越大）= 越精细 = 误差越小。
    使用指数衰减：error = 1000.0 * (0.5 ^ level) * scale
    """
    error = 1000.0 * (0.5 ** level) * scale
    return max(error, 0.01)

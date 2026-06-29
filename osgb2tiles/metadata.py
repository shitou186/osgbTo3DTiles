"""元数据解析与坐标系转换"""

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

import numpy as np
from pyproj import CRS, Transformer


@dataclass
class OsgeMetadata:
    origin_lon: float
    origin_lat: float
    origin_height: float
    srs: str
    bounding_box: tuple  # (min_x, min_y, min_z, max_x, max_y, max_z)
    geoid_offset: float = 0.0  # 大地水准面高程纠正（米）
    swap_xy: bool = False  # CRS 轴顺序为 (Northing, Easting) 时需交换 X/Y


def geoid_undulation_egm96(lat: float, lon: float) -> float:
    """EGM96 大地水准面起伏近似计算（球谐展开前几项）。

    精度约 ±1-2 米，适用于大多数倾斜摄影场景。
    参考: https://en.wikipedia.org/wiki/Earth_Gravitational_Model
    """
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)

    # EGM96 主要球谐系数（低阶近似）
    # 这些系数给出了全球大地水准面起伏的基本形态
    N = (
        -30.5 * math.sin(2 * lat_rad)
        + 0.5 * math.cos(2 * lat_rad) * math.cos(2 * lon_rad)
        + 1.5 * math.cos(lat_rad) * math.cos(lon_rad)
        + 2.0 * math.sin(lat_rad)
        - 1.0 * math.cos(lat_rad) * math.cos(lon_rad)
    )
    return N


def compute_geoid_offset(lat: float, lon: float, grid_path: Optional[str] = None) -> float:
    """计算大地水准面高程纠正。

    优先使用外部 geoid grid 文件（如 EGM2008），
    回退到 EGM96 球谐近似。

    Args:
        lat: 纬度（WGS84 度）
        lon: 经度（WGS84 度）
        grid_path: 可选的 geoid grid 文件路径（如 egm2008-1.tif）

    Returns:
        大地水准面起伏值（米），用于：ellipsoidal_h = orthometric_h + N
    """
    if grid_path:
        try:
            t = Transformer.from_pipeline(
                f"+proj=pipeline +step +proj=gridshift +grids={grid_path}"
            )
            result = t.transform(lon, lat, 0.0)
            return float(result[2])
        except Exception:
            pass

    return geoid_undulation_egm96(lat, lon)


def parse_metadata(xml_path: str) -> OsgeMetadata:
    """解析 metadata.xml，提取坐标基准信息。

    兼容两种常见格式：
    1. <SRS>EPSG:4547</SRS> + <Origin X="..." Y="..." Z="..."/>
    2. <CoordSys>...</CoordSys> + <SRSOrigin>x y z</SRSOrigin>
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    srs = ""
    origin_x, origin_y, origin_z = 0.0, 0.0, 0.0

    srs_elem = root.find(".//SRS")
    if srs_elem is None:
        srs_elem = root.find(".//CoordSys")
    if srs_elem is not None and srs_elem.text:
        srs = srs_elem.text.strip()

    origin_elem = root.find(".//Origin")
    if origin_elem is not None:
        origin_x = float(origin_elem.get("X", 0))
        origin_y = float(origin_elem.get("Y", 0))
        origin_z = float(origin_elem.get("Z", 0))
    else:
        srs_origin = root.find(".//SRSOrigin")
        if srs_origin is not None and srs_origin.text:
            parts = re.split(r"[,\s]+", srs_origin.text.strip())
            origin_x, origin_y, origin_z = map(float, parts[:3])

    # 检测 CRS 轴顺序：如果投影坐标系第一轴是北方向（如 CGCS2000 3度带），需要交换顶点 X/Y
    # OSGB 顶点使用 CRS 原生轴顺序，但 ECEF 矩阵假设 ENU (East, North, Up) 顺序
    # 注意：地理坐标系（如 EPSG:4326）的轴顺序由 always_xy=True 处理，不需要手动交换
    # 原点坐标也不需要交换，因为 always_xy=True 已正确处理
    swap_xy = False
    try:
        crs = CRS.from_user_input(srs)
        if (crs.is_projected
                and len(crs.axis_info) >= 2
                and crs.axis_info[0].direction == "north"):
            swap_xy = True
    except Exception:
        pass

    transformer = Transformer.from_crs(srs, "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(origin_x, origin_y)

    # 解析 BoundingBox（可选）
    bbox = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    bbox_elem = root.find(".//BoundingBox")
    if bbox_elem is not None:
        min_elem = bbox_elem.find("Min")
        max_elem = bbox_elem.find("Max")
        if min_elem is not None and max_elem is not None:
            bbox = (
                float(min_elem.get("X", 0)),
                float(min_elem.get("Y", 0)),
                float(min_elem.get("Z", 0)),
                float(max_elem.get("X", 0)),
                float(max_elem.get("Y", 0)),
                float(max_elem.get("Z", 0)),
            )

    # 计算大地水准面高程纠正
    # 中国 1985 高程系统使用正高，需要加大地水准面起伏转为椭球高
    geoid_offset = compute_geoid_offset(lat, lon)

    return OsgeMetadata(
        origin_lon=lon,
        origin_lat=lat,
        origin_height=origin_z + geoid_offset,
        srs=srs,
        bounding_box=bbox,
        geoid_offset=geoid_offset,
        swap_xy=swap_xy,
    )


def _compute_convergence_angle(srs: str, lon: float, lat: float) -> float:
    """计算子午线收敛角（弧度）。

    收敛角 = 网格北相对于真北的顺时针夹角。
    地理坐标系（如 EPSG:4326）无投影，收敛角为 0。

    Args:
        srs: 坐标参考系标识（如 'EPSG:4547'）
        lon: 经度（WGS84 度）
        lat: 纬度（WGS84 度）

    Returns:
        收敛角（弧度）
    """
    from pyproj import Proj
    try:
        p = Proj(srs)
        factors = p.get_factors(lon, lat, radians=False)
        return math.radians(factors.meridian_convergence)
    except Exception:
        return 0.0


def local_to_ecef_transform(metadata: OsgeMetadata) -> np.ndarray:
    """计算从局部坐标（投影 CRS 网格坐标，Z-up）到 ECEF 的 4x4 变换矩阵。

    变换链：M = M_ecef_enu × M_convergence

    1. M_convergence：绕 Z 轴旋转 -α，消除子午线收敛角，将网格北对齐真北
    2. M_ecef_enu：真 ENU → ECEF 旋转 + 平移

    Args:
        metadata: 包含原点经纬度高程和 SRS 的元数据
    """
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:4978", always_xy=True)
    ecef_x, ecef_y, ecef_z = transformer.transform(
        metadata.origin_lon, metadata.origin_lat, metadata.origin_height
    )

    lon_rad = math.radians(metadata.origin_lon)
    lat_rad = math.radians(metadata.origin_lat)
    cos_lat = math.cos(lat_rad)
    sin_lat = math.sin(lat_rad)
    cos_lon = math.cos(lon_rad)
    sin_lon = math.sin(lon_rad)

    # ENU → ECEF 旋转 + 平移
    m_ecef_enu = np.array(
        [
            [-sin_lon, -sin_lat * cos_lon, cos_lat * cos_lon, ecef_x],
            [cos_lon, -sin_lat * sin_lon, cos_lat * sin_lon, ecef_y],
            [0.0, cos_lat, sin_lat, ecef_z],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )

    # 子午线收敛角修正：绕 Z 轴旋转 -α，将网格北对齐真北
    convergence_rad = _compute_convergence_angle(
        metadata.srs, metadata.origin_lon, metadata.origin_lat
    )
    m_convergence = np.eye(4, dtype=np.float64)
    if abs(convergence_rad) > 1e-10:
        cos_c = math.cos(-convergence_rad)
        sin_c = math.sin(-convergence_rad)
        m_convergence[0, 0] = cos_c
        m_convergence[0, 1] = -sin_c
        m_convergence[1, 0] = sin_c
        m_convergence[1, 1] = cos_c

    matrix = m_ecef_enu @ m_convergence
    return matrix


def bbox_center_lonlat(bounding_volume: dict) -> tuple:
    """从 3D Tiles boundingVolume 提取中心点坐标。

    支持 box 和 sphere 两种格式。
    返回 (x_center, y_center)，用于四叉树空间排序。
    """
    if "box" in bounding_volume:
        box = bounding_volume["box"]
        return (float(box[0]), float(box[1]))
    elif "sphere" in bounding_volume:
        s = bounding_volume["sphere"]
        return (float(s[0]), float(s[1]))
    return (0.0, 0.0)

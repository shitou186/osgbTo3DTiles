"""元数据解析与坐标系转换"""

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import numpy as np
from pyproj import Transformer


@dataclass
class OsgeMetadata:
    origin_lon: float
    origin_lat: float
    origin_height: float
    srs: str
    bounding_box: tuple  # (min_x, min_y, min_z, max_x, max_y, max_z)


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

    transformer = Transformer.from_crs(srs, "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(origin_x, origin_y)

    return OsgeMetadata(
        origin_lon=lon,
        origin_lat=lat,
        origin_height=origin_z,
        srs=srs,
        bounding_box=(0, 0, 0, 0, 0, 0),
    )


def local_to_ecef_transform(metadata: OsgeMetadata) -> np.ndarray:
    """计算从局部 ENU 坐标到 ECEF 的 4x4 变换矩阵。"""
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
    matrix = np.array(
        [
            [-sin_lon, -sin_lat * cos_lon, cos_lat * cos_lon, ecef_x],
            [cos_lon, -sin_lat * sin_lon, cos_lat * sin_lon, ecef_y],
            [0.0, cos_lat, sin_lat, ecef_z],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    return matrix


def local_to_wgs84_transform(metadata: OsgeMetadata) -> np.ndarray:
    """计算从局部 ENU 坐标到 WGS84 的 4x4 变换矩阵。"""
    lon_rad = math.radians(metadata.origin_lon)
    lat_rad = math.radians(metadata.origin_lat)
    cos_lat = math.cos(lat_rad)
    sin_lat = math.sin(lat_rad)
    cos_lon = math.cos(lon_rad)
    sin_lon = math.sin(lon_rad)

    matrix = np.array(
        [
            [-sin_lon, -sin_lat * cos_lon, cos_lat * cos_lon, metadata.origin_lon],
            [cos_lon, -sin_lat * sin_lon, cos_lat * sin_lon, metadata.origin_lat],
            [0.0, cos_lat, sin_lat, metadata.origin_height],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
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

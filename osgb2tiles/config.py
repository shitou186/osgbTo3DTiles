from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class TextureFormat(Enum):
    JPG = "jpg"
    WEBP = "webp"
    KTX2 = "ktx2"


class RefineMode(Enum):
    ADD = "ADD"
    REPLACE = "REPLACE"


@dataclass
class LODLevelSettings:
    """单个 LOD 级别的独立配置（参考 fanvanzh/3dtiles 的 LODLevelSettings）。"""
    ratio: float                    # 网格简化比例 (0.0, 1.0]
    texture_size: int = 0           # 纹理尺寸（0=自动计算）
    use_draco: bool = False         # 该级别是否应用 Draco 压缩
    geometric_error: float = 0.0    # 该级别的几何误差（0=自动计算）


@dataclass
class ConvertConfig:
    back_face_culling: bool = True
    force_double_sided: bool = False
    unlit_shading: bool = True
    texture_format: TextureFormat = TextureFormat.JPG
    mesh_compression: bool = False
    output_format: str = "glb"
    refine_mode: RefineMode = RefineMode.REPLACE
    geometric_error_scale: float = 1.0
    max_texture_size: int = 2048
    threads: int = 8
    ecef_transform: bool = True

    # LOD 与简化参数
    enable_lod: bool = False
    enable_simplify: bool = False
    lod_levels: List[float] = field(default_factory=lambda: [1.0, 0.5, 0.25])
    simplify_error: float = 0.01

    # Draco 与纹理压缩（mesh_compression 保留向后兼容）
    enable_draco: bool = False
    enable_texture_compress: bool = False

    # 高级 LOD 配置（与 lod_levels 互斥）
    lod_level_settings: Optional[List[LODLevelSettings]] = None

    # 输出格式版本
    tiles_version: str = "1.1"  # "1.0" (b3dm) 或 "1.1" (glb)

    # 坐标精度
    precise_coords: bool = False  # 逐顶点坐标纠正

    def __post_init__(self):
        """同步 enable_draco 与 mesh_compression。"""
        if self.mesh_compression and not self.enable_draco:
            self.enable_draco = True
        if self.enable_draco and not self.mesh_compression:
            self.mesh_compression = True

    def validate(self):
        if self.back_face_culling and self.force_double_sided:
            raise ValueError("back_face_culling 与 force_double_sided 互斥")
        if self.enable_lod and not self.lod_levels:
            raise ValueError("启用 LOD 时必须指定 lod_levels")
        if any(r <= 0 or r > 1.0 for r in self.lod_levels):
            raise ValueError("lod_levels 各级别比例须在 (0, 1.0] 范围内")
        if self.lod_level_settings is not None and self.lod_levels != [1.0, 0.5, 0.25]:
            raise ValueError("lod_level_settings 与 lod_levels 互斥，只能使用其一")

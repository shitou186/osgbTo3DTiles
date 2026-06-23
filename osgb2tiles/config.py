from dataclasses import dataclass, field
from enum import Enum
from typing import List


class TextureFormat(Enum):
    JPG = "jpg"
    WEBP = "webp"
    KTX2 = "ktx2"


class RefineMode(Enum):
    ADD = "ADD"
    REPLACE = "REPLACE"


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

    # 输出格式版本
    tiles_version: str = "1.1"  # "1.0" (b3dm) 或 "1.1" (glb)

    def validate(self):
        if self.back_face_culling and self.force_double_sided:
            raise ValueError("back_face_culling 与 force_double_sided 互斥")
        if self.enable_lod and not self.lod_levels:
            raise ValueError("启用 LOD 时必须指定 lod_levels")
        if any(r <= 0 or r > 1.0 for r in self.lod_levels):
            raise ValueError("lod_levels 各级别比例须在 (0, 1.0] 范围内")

from dataclasses import dataclass
from enum import Enum


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

    def validate(self):
        if self.back_face_culling and self.force_double_sided:
            raise ValueError("back_face_culling 与 force_double_sided 互斥")

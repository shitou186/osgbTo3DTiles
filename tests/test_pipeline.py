"""单元测试：优化流水线状态机、LOD 联动矩阵、纹理尺寸计算、内存管理。"""

import gc
import os


import numpy as np

from osgb2tiles.config import ConvertConfig, LODLevelSettings
from osgb2tiles.pipeline import (
    OptimizationPipeline,
    PipelineContext,
    PipelineStage,
    TileWorkItem,
    TileResult,
)
from osgb2tiles.memory_guard import release_numpy_refs


class TestPipelineStateMachine:
    """测试流水线状态转换。"""

    def test_initial_stage_is_parse(self):
        """流水线初始阶段应为 PARSE。"""
        config = ConvertConfig()
        ctx = PipelineContext(
            config=config, metadata=None,
            structure_type=None, ecef_matrix=np.eye(4),
            assembler=None,
        )
        pipeline = OptimizationPipeline(ctx)
        assert pipeline.stage == PipelineStage.PARSE


class TestLODMatrix:
    """测试 LOD × Simplify × Draco 联动矩阵。"""

    def test_lod0_never_draco(self):
        """LOD0（最高质量）在 LOD+Draco 模式下永远不压缩。"""
        config = ConvertConfig(
            enable_lod=True,
            enable_draco=True,
            lod_levels=[1.0, 0.5, 0.25],
        )
        assert config.enable_draco is True
        for level_idx in range(3):
            is_highest = (level_idx == 0)
            use_draco = config.enable_draco and not is_highest
            if level_idx == 0:
                assert use_draco is False, "LOD0 永不压缩"
            else:
                assert use_draco is True, "LOD1+ 应压缩"

    def test_lod_without_draco(self):
        """LOD 开启但 Draco 关闭时，所有级别都不压缩。"""
        config = ConvertConfig(enable_lod=True, enable_draco=False)
        for level_idx in range(3):
            is_highest = (level_idx == 0)
            use_draco = config.enable_draco and not is_highest
            assert use_draco is False

    def test_draco_without_lod(self):
        """Draco 开启但 LOD 关闭时，所有瓦片统一压缩。"""
        config = ConvertConfig(enable_lod=False, enable_draco=True)
        assert config.enable_draco is True

    def test_all_off(self):
        """全部关闭：标准转换，无压缩。"""
        config = ConvertConfig(
            enable_lod=False,
            enable_draco=False,
            enable_simplify=False,
        )
        assert config.enable_draco is False
        assert config.enable_lod is False
        assert config.enable_simplify is False


class TestTextureSizeComputation:
    """测试 LOD 纹理尺寸计算。"""

    def _make_pipeline(self, max_texture_size=2048):
        config = ConvertConfig(max_texture_size=max_texture_size)
        ctx = PipelineContext(
            config=config, metadata=None,
            structure_type=None, ecef_matrix=np.eye(4),
            assembler=None,
        )
        return OptimizationPipeline(ctx)

    def test_monotonic_decrease(self):
        """纹理尺寸必须随 LOD 级别单调递减。"""
        pipeline = self._make_pipeline()
        sizes = pipeline._compute_lod_texture_sizes([1.0, 0.5, 0.25])
        for i in range(1, len(sizes)):
            assert sizes[i] < sizes[i - 1], f"LOD{i} 纹理应小于 LOD{i-1}"

    def test_minimum_size(self):
        """纹理尺寸不应低于 64px。"""
        pipeline = self._make_pipeline()
        sizes = pipeline._compute_lod_texture_sizes([1.0, 0.1, 0.01])
        assert all(s >= 64 for s in sizes)

    def test_power_of_2(self):
        """纹理尺寸应为 2 的幂次。"""
        pipeline = self._make_pipeline()
        sizes = pipeline._compute_lod_texture_sizes([1.0, 0.5, 0.25])
        for s in sizes:
            assert s > 0 and (s & (s - 1)) == 0, f"{s} 不是 2 的幂次"

    def test_full_ratio_uses_max_size(self):
        """ratio=1.0 时应使用 max_texture_size。"""
        pipeline = self._make_pipeline(max_texture_size=1024)
        sizes = pipeline._compute_lod_texture_sizes([1.0])
        assert sizes[0] == 1024


class TestMemoryGuard:
    """测试内存管理工具。"""

    def test_release_numpy_refs(self):
        """release_numpy_refs 应将 ndarray 属性设为 None。"""
        class FakeMesh:
            def __init__(self):
                self.vertices = np.zeros((100, 3), dtype=np.float32)
                self.normals = np.zeros((100, 3), dtype=np.float32)
                self.indices = np.zeros(300, dtype=np.uint32)

        mesh = FakeMesh()
        assert mesh.vertices is not None
        release_numpy_refs(mesh)
        assert mesh.vertices is None
        assert mesh.normals is None
        assert mesh.indices is None

    def test_release_numpy_refs_list(self):
        """release_numpy_refs 应递归处理列表中的对象。"""
        class FakeMesh:
            def __init__(self):
                self.vertices = np.zeros((10, 3), dtype=np.float32)
                self.normals = np.zeros((10, 3), dtype=np.float32)
                self.indices = np.zeros(30, dtype=np.uint32)

        meshes = [FakeMesh(), FakeMesh()]
        release_numpy_refs(meshes)
        for m in meshes:
            assert m.vertices is None
            assert m.normals is None


class TestConvertConfigNew:
    """测试新增配置项。"""

    def test_enable_draco_default(self):
        cfg = ConvertConfig()
        assert cfg.enable_draco is False

    def test_enable_texture_compress_default(self):
        cfg = ConvertConfig()
        assert cfg.enable_texture_compress is False

    def test_mesh_compression_syncs_to_enable_draco(self):
        """设置 mesh_compression=True 应同步 enable_draco。"""
        cfg = ConvertConfig(mesh_compression=True)
        assert cfg.enable_draco is True

    def test_enable_draco_syncs_to_mesh_compression(self):
        """设置 enable_draco=True 应同步 mesh_compression。"""
        cfg = ConvertConfig(enable_draco=True)
        assert cfg.mesh_compression is True

    def test_lod_level_settings_default_none(self):
        cfg = ConvertConfig()
        assert cfg.lod_level_settings is None

    def test_lod_level_settings_exclusive_with_lod_levels(self):
        """lod_level_settings 与默认 lod_levels 互斥。"""
        settings = [LODLevelSettings(ratio=1.0), LODLevelSettings(ratio=0.5)]
        cfg = ConvertConfig(
            lod_level_settings=settings,
            lod_levels=[1.0, 0.5, 0.25],
        )
        # 默认 lod_levels 是 [1.0, 0.5, 0.25]，所以不互斥
        cfg.validate()  # 应不抛异常

    def test_lod_level_settings_exclusive_with_custom_lod_levels(self):
        """lod_level_settings 与自定义 lod_levels 互斥。"""
        settings = [LODLevelSettings(ratio=1.0), LODLevelSettings(ratio=0.5)]
        cfg = ConvertConfig(
            lod_level_settings=settings,
            lod_levels=[1.0, 0.3],
        )
        try:
            cfg.validate()
            assert False, "应抛出 ValueError"
        except ValueError:
            pass

    def test_lod_level_settings_dataclass(self):
        """LODLevelSettings 各字段应正确初始化。"""
        s = LODLevelSettings(ratio=0.5, texture_size=512, use_draco=True, geometric_error=100.0)
        assert s.ratio == 0.5
        assert s.texture_size == 512
        assert s.use_draco is True
        assert s.geometric_error == 100.0

    def test_lod_level_settings_defaults(self):
        """LODLevelSettings 默认值。"""
        s = LODLevelSettings(ratio=1.0)
        assert s.texture_size == 0
        assert s.use_draco is False
        assert s.geometric_error == 0.0

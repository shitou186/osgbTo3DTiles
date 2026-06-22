"""单元测试：OSGB 目录结构检测与路径解析。"""

import os
import tempfile

from osgb2tiles.structure import (
    StructureType,
    detect_structure,
    find_root_osgb,
    resolve_pagelod_path,
    extract_level_from_filename,
    compute_level_based_error,
    find_data_dir,
)


class TestDetectStructure:
    def test_context_capture_with_data_osgb(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = os.path.join(tmpdir, "Data")
            os.makedirs(data_dir)
            open(os.path.join(data_dir, "Data.osgb"), "w").close()
            assert detect_structure(tmpdir) == StructureType.CONTEXT_CAPTURE

    def test_context_capture_with_base_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = os.path.join(tmpdir, "Data")
            os.makedirs(os.path.join(data_dir, "Base"))
            open(os.path.join(data_dir, "SomeTile.osgb"), "w").close()
            assert detect_structure(tmpdir) == StructureType.CONTEXT_CAPTURE

    def test_dji_terra_flat_numeric(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = os.path.join(tmpdir, "Data")
            numeric_dir = os.path.join(data_dir, "314341526340")
            os.makedirs(numeric_dir)
            open(os.path.join(numeric_dir, "314341526340.osgb"), "w").close()
            open(os.path.join(numeric_dir, "3143415263404.osgb"), "w").close()
            assert detect_structure(tmpdir) == StructureType.DJI_TERRA

    def test_dji_terra_direct_numeric(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "314341526340.osgb"), "w").close()
            open(os.path.join(tmpdir, "3143415263404.osgb"), "w").close()
            assert detect_structure(tmpdir) == StructureType.DJI_TERRA

    def test_unknown_structure_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "Data"))
            try:
                detect_structure(tmpdir)
                assert False, "应抛出 ValueError"
            except ValueError:
                pass


class TestFindDataDir:
    def test_osgb_in_input_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "root.osgb"), "w").close()
            assert find_data_dir(tmpdir) == tmpdir

    def test_osgb_in_data_subdir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = os.path.join(tmpdir, "Data")
            os.makedirs(data_dir)
            open(os.path.join(data_dir, "root.osgb"), "w").close()
            assert find_data_dir(tmpdir) == data_dir

    def test_nested_numeric_subdir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = os.path.join(tmpdir, "Data")
            numeric_dir = os.path.join(data_dir, "314341526340")
            os.makedirs(numeric_dir)
            open(os.path.join(numeric_dir, "314341526340.osgb"), "w").close()
            assert find_data_dir(tmpdir) == numeric_dir


class TestFindRootOsgb:
    def test_context_capture_data_osgb(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = os.path.join(tmpdir, "Data")
            os.makedirs(data_dir)
            open(os.path.join(data_dir, "Data.osgb"), "w").close()
            result = find_root_osgb(tmpdir, StructureType.CONTEXT_CAPTURE)
            assert result.endswith("Data.osgb")

    def test_context_capture_missing_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = os.path.join(tmpdir, "Data")
            os.makedirs(data_dir)
            try:
                find_root_osgb(tmpdir, StructureType.CONTEXT_CAPTURE)
                assert False, "应抛出 FileNotFoundError"
            except FileNotFoundError:
                pass

    def test_dji_terra_shortest_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = os.path.join(tmpdir, "Data")
            numeric_dir = os.path.join(data_dir, "314341526340")
            os.makedirs(numeric_dir)
            open(os.path.join(numeric_dir, "314341526340.osgb"), "w").close()
            open(os.path.join(numeric_dir, "3143415263404.osgb"), "w").close()
            open(os.path.join(numeric_dir, "31434152634042.osgb"), "w").close()
            result = find_root_osgb(tmpdir, StructureType.DJI_TERRA)
            assert result.endswith("314341526340.osgb")


class TestResolvePagelodPath:
    def test_context_capture_nested_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = os.path.join(tmpdir, "Base")
            os.makedirs(base_dir)
            open(os.path.join(base_dir, "Tile_001_L1_0.osgb"), "w").close()
            result = resolve_pagelod_path(
                tmpdir, "Base/Tile_001_L1_0.osgb",
                StructureType.CONTEXT_CAPTURE
            )
            assert os.path.exists(result)

    def test_context_capture_same_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "Tile_001.osgb"), "w").close()
            result = resolve_pagelod_path(
                tmpdir, "Tile_001.osgb",
                StructureType.CONTEXT_CAPTURE
            )
            assert os.path.exists(result)

    def test_dji_terra_flat_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "3143415263404.osgb"), "w").close()
            result = resolve_pagelod_path(
                tmpdir, "3143415263404.osgb",
                StructureType.DJI_TERRA,
                osgb_path=os.path.join(tmpdir, "314341526340.osgb")
            )
            assert os.path.exists(result)

    def test_dji_terra_strips_directory_prefix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "3143415263404.osgb"), "w").close()
            result = resolve_pagelod_path(
                tmpdir, "some/dir/3143415263404.osgb",
                StructureType.DJI_TERRA
            )
            assert os.path.exists(result)


class TestExtractLevel:
    def test_context_capture_level(self):
        assert extract_level_from_filename(
            "Tile_001_L15_0.osgb", StructureType.CONTEXT_CAPTURE
        ) == 15

    def test_context_capture_level_0(self):
        assert extract_level_from_filename(
            "Tile_001_L0_0.osgb", StructureType.CONTEXT_CAPTURE
        ) == 0

    def test_context_capture_no_level(self):
        assert extract_level_from_filename(
            "Data.osgb", StructureType.CONTEXT_CAPTURE
        ) is None

    def test_dji_terra_level_from_length(self):
        assert extract_level_from_filename(
            "3143415263404.osgb", StructureType.DJI_TERRA, root_name_length=12
        ) == 1

    def test_dji_terra_deep_level(self):
        assert extract_level_from_filename(
            "314341526340424365140.osgb", StructureType.DJI_TERRA, root_name_length=12
        ) == 9

    def test_dji_terra_root_level(self):
        assert extract_level_from_filename(
            "314341526340.osgb", StructureType.DJI_TERRA, root_name_length=12
        ) == 0

    def test_dji_terra_non_numeric(self):
        assert extract_level_from_filename(
            "random.osgb", StructureType.DJI_TERRA, root_name_length=12
        ) is None


class TestComputeLevelBasedError:
    def test_level_0(self):
        assert compute_level_based_error(0) == 1000.0

    def test_level_1(self):
        assert compute_level_based_error(1) == 500.0

    def test_level_10(self):
        error = compute_level_based_error(10)
        assert error < 1.0
        assert error > 0.0

    def test_with_scale(self):
        assert compute_level_based_error(0, scale=0.5) == 500.0

    def test_minimum_value(self):
        error = compute_level_based_error(100)
        assert error == 0.01

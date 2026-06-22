# AGENTS.md

## Project overview

Python CLI tool that converts OSGB (OpenSceneGraph Binary) oblique photogrammetry data into 3D Tiles 1.1 format (tileset.json + GLB tiles). All source code and comments are in Chinese.

## Commands

```bash
# Install dependencies (creates venv/ automatically)
./run.sh -i <osgb_dir> -o <output_dir>

# Manual setup
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m osgb2tiles -i <osgb_dir> -o <output_dir>

# Run tests (./test.sh auto-creates venv and installs pytest)
./test.sh -v

# Manual test
PYTHONPATH=. pytest tests/ -v
```

Note: `PYTHONPATH=.` is required when running pytest directly (no `pyproject.toml` / `setup.py`).

## Architecture

Single package `osgb2tiles/` with no build system (no pyproject.toml or setup.py).

- `cli.py` — argparse CLI entrypoint; finds `metadata.xml` and root OSGB via `structure.py`
- `config.py` — `ConvertConfig` dataclass; `back_face_culling` and `force_double_sided` are mutually exclusive (validated)
- `metadata.py` — parses `metadata.xml` (two XML formats supported); converts local CRS → EPSG:4326 → ECEF via pyproj; `SRSOrigin` supports both comma and space separators
- `structure.py` — directory structure detection (ContextCapture nested vs DJI Terra flat), root OSGB discovery, PagedLOD path resolution, level extraction from filenames
- `osgb_parser.py` — partial OSGB binary parser (magic `osg`, reads Group/Geode/Geometry/PagedLOD); **not production-complete** — the docstring explicitly recommends a C++ binding for full format coverage. DJI Terra OSGB files use a different binary format (28-byte wrapper header + class-name-based serialization); the parser can detect DJI format and extract child tile references, but cannot extract vertex/index geometry data — produces 0 actual tiles.
- `gltf_assembler.py` — builds glTF 2.0 JSON + BIN, packs into GLB binary; supports KHR_materials_unlit, KHR_texture_basisu, EXT_texture_webp, KHR_draco_mesh_compression
- `texture.py` — Pillow-based resize/encode; KTX2 encoding shells out to `toktx` (KTX-Software CLI)
- `tileset_builder.py` — recursive OSGB tree → tileset.json builder; `build()` is the main entry

## Key conventions

- All docstrings, comments, error messages, and CLI help text are in Chinese — maintain this when editing.
- Tests use pytest with class-based test groups (no fixtures, no conftest). No `__init__.py` in `tests/`.
- OSGB input supports two directory structures (auto-detected by `structure.py`):
  - ContextCapture: `Data.osgb` root with optional `Base/` subdirectory
  - DJI Terra: flat numeric-named `.osgb` files in a single subdirectory
- Texture path resolution is relative to the OSGB file's directory first, then CWD.
- The root tile always gets an ECEF transform (`ecef_transform=True` hardcoded in cli.py).

## Optional dependencies

- `--texture ktx2` requires `toktx` on PATH (from KTX-Software)
- `--draco` requires `pip install draco` (Python bindings for Google Draco)

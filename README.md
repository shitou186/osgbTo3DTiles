# OSGB2Tiles

OSGB 倾斜摄影数据 → 3D Tiles 1.1 转换工具。将 OSGB 格式的实景三维模型转换为 3D Tiles 标准格式（`tileset.json` + GLB 瓦片），可直接在 Cesium 等三维地球引擎中加载。

## 功能特性

- 解析 OSGB 二进制格式（Group / Geode / Geometry / PagedLOD）
- 自动生成符合 3D Tiles 1.1 规范的 `tileset.json`
- 支持 ECEF 坐标变换（局部坐标 → 地心坐标）
- 元数据解析（兼容两种常见 `metadata.xml` 格式，pyproj 坐标转换）
- glTF 2.0 / GLB 输出，支持多种纹理格式：JPG、WebP、KTX2
- 可选 Draco 网格压缩
- 材质控制：Unlit 着色、背面裁切、双面渲染

## 快速开始

```bash
# 1. 克隆项目
git clone <repo-url> && cd osgbTo3DTiles

# 2. 一键运行（自动创建虚拟环境并安装依赖）
./run.sh -i ./Data -o ./output

# 或手动安装
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m osgb2tiles -i ./Data -o ./output
```

输入目录结构要求：

```
Data/
├── Data.osgb          # 根瓦片文件（必需）
├── metadata.xml       # 坐标元数据（可选，推荐）
├── Tile_000001.osgb   # 子瓦片
├── Tile_000002.osgb
└── ...
```

## 命令行参数

```
python -m osgb2tiles -i <输入目录> -o <输出目录> [选项]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-i`, `--input` | （必填） | OSGB 数据目录 |
| `-o`, `--output` | （必填） | 输出目录 |
| `--texture` | `jpg` | 纹理格式：`jpg` / `webp` / `ktx2` |
| `--max-texture-size` | `2048` | 纹理最大尺寸（像素） |
| `--draco` | 关闭 | 启用 Draco 网格压缩 |
| `--unlit` / `--no-unlit` | 开启 | 无光照着色 |
| `--back-face-culling` / `--no-back-face-culling` | 开启 | 背面裁切 |
| `--double-sided` | 关闭 | 强制双面渲染（与背面裁切互斥） |
| `--refine` | `REPLACE` | 细化模式：`ADD` / `REPLACE` |
| `--error-scale` | `1.0` | 几何误差缩放因子 |
| `--threads` | `8` | 并行线程数 |

### 使用示例

```bash
# 基本转换
python -m osgb2tiles -i ./Data -o ./output

# 使用 WebP 纹理 + 双面渲染
python -m osgb2tiles -i ./Data -o ./output --texture webp --no-unlit

# Draco 压缩 + KTX2 纹理
python -m osgb2tiles -i ./Data -o ./output --draco --texture ktx2 --double-sided
```

## 测试

```bash
# 一键运行测试（自动安装 pytest）
./test.sh

# 或手动运行
source venv/bin/activate
PYTHONPATH=. pytest tests/ -v
```

## 项目结构

```
osgb2tiles/
├── __init__.py          # 版本号
├── __main__.py          # python -m 入口
├── cli.py               # argparse CLI，查找 metadata.xml 和根 OSGB
├── config.py            # ConvertConfig 配置数据类
├── metadata.py          # metadata.xml 解析 + CRS → ECEF 坐标变换
├── osgb_parser.py       # OSGB 二进制格式解析器
├── gltf_assembler.py    # glTF 2.0 / GLB 组装
├── texture.py           # 纹理加载、缩放、格式编码
└── tileset_builder.py   # 递归构建 tileset.json
```

## 可选依赖

| 功能 | 依赖 | 安装方式 |
|------|------|----------|
| KTX2 纹理 | `toktx` | [KTX-Software](https://github.com/KhronosGroup/KTX-Software) |
| Draco 压缩 | `draco` | `pip install draco` |

## 技术细节

- **坐标变换**：通过 pyproj 将 `metadata.xml` 中的局部坐标系（如 CGCS2000 高斯投影）转换为 EPSG:4326 再转 ECEF
- **GLB 打包**：严格遵循 glTF 2.0 规范，JSON/BIN 块 4 字节对齐
- **几何误差**：基于 OSGB PagedLOD 的 `range_max` 映射为 3D Tiles 的 `geometricError`
- **OSGB 解析**：当前为简化实现，完整格式兼容建议对接 OpenSceneGraph C++ 库

## 许可

本项目仅供学习和研究使用。

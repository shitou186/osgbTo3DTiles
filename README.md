# OSGB2Tiles

OSGB 倾斜摄影数据 → 3D Tiles 转换工具。支持 3D Tiles 1.0 (b3dm) 与 1.1 (glb) 双版本输出，可直接在 Cesium 等三维地球引擎中加载。

## 功能特性

- **多源数据支持**：ContextCapture（含分幅 `Tile_+xxx_+xxx` 目录）和 DJI Terra OSGB 格式自动识别
- **双版本输出**：`--format-version 1.0` 输出 b3dm（向下兼容），`1.1` 输出 glb（默认）
- **多级 LOD**：`--enable-lod` 生成倒置树结构的多级细节层级
- **网格简化**：`--enable-simplify` 基于 meshoptimizer 的自适应三角形简化
- **条件 Draco 压缩**：LOD 模式下 LOD0 不压缩（保精度），LOD1+ 自动 Draco 压缩
- **多材质纹理**：单瓦片多材质组自动匹配嵌入 JPEG 纹理
- **坐标变换**：pyproj CRS → EPSG:4326 → ECEF，Z-up → Y-up 坐标系转换
- **材质控制**：Unlit 着色、背面裁切、双面渲染

## 快速开始

```bash
# 一键运行（自动创建虚拟环境并安装依赖）
./run.sh -i ./Data -o ./output

# 或使用 Makefile
make run INPUT=./Data OUTPUT=./output

# 手动安装
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m osgb2tiles -i ./Data -o ./output
```

### 支持的输入目录结构

**ContextCapture 标准格式**：
```
Data/
├── Data.osgb              # 根瓦片
├── metadata.xml
├── Tile_000001.osgb
└── ...
```

**ContextCapture 分幅格式**：
```
Data/
├── tileset.osgb           # 根索引
├── top/Level_18/          # LOD 层级
├── top/Level_19/
├── Tile_+000_+000/        # 分幅瓦片 + _L 层级文件
├── Tile_+001_+000/
└── metadata.xml
```

**DJI Terra 格式**：
```
Data/
├── 314341526340/          # 数字命名目录
│   ├── 314341526340.osgb  # 根文件（最短文件名）
│   ├── 3143415263404.osgb
│   └── ...
└── metadata.xml
```

## 命令行参数

```
python -m osgb2tiles -i <输入目录> -o <输出目录> [选项]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-i`, `--input` | （必填） | OSGB 数据目录 |
| `-o`, `--output` | （必填） | 输出目录 |
| `--format-version` | `1.1` | 3D Tiles 版本：`1.0` (b3dm) / `1.1` (glb) |
| `--texture` | `jpg` | 纹理格式：`jpg` / `webp` / `ktx2` |
| `--max-texture-size` | `2048` | 纹理最大尺寸（像素） |
| `--draco` | 关闭 | 启用 Draco 网格压缩 |
| `--enable-lod` | 关闭 | 启用多级细节（LOD） |
| `--enable-simplify` | 关闭 | 启用网格简化（需 meshoptimizer） |
| `--lod-levels` | `1.0,0.5,0.25` | LOD 级别比例，逗号分隔 |
| `--simplify-error` | `0.01` | 简化最大允许误差 |
| `--unlit` / `--no-unlit` | 开启 | 无光照着色 |
| `--back-face-culling` / `--no-back-face-culling` | 开启 | 背面裁切 |
| `--double-sided` | 关闭 | 强制双面渲染（与背面裁切互斥） |
| `--refine` | `REPLACE` | 细化模式：`ADD` / `REPLACE` |
| `--error-scale` | `1.0` | 几何误差缩放因子 |
| `--threads` | `8` | 并行线程数 |

## 使用示例

```bash
# 基本转换
./run.sh -i ./Data -o ./output

# 3D Tiles 1.0 b3dm 格式输出
./run.sh -i ./Data -o ./output --format-version 1.0

# LOD + 简化 + Draco 联动
./run.sh -i ./Data -o ./output --enable-lod --enable-simplify --draco

# 自定义 LOD 级别
./run.sh -i ./Data -o ./output --enable-lod --lod-levels 1.0,0.7,0.3,0.1

# WebP 纹理 + 双面渲染
./run.sh -i ./Data -o ./output --texture webp --double-sided

# 完整联动：1.0 + LOD + 简化 + Draco + KTX2
./run.sh -i ./Data -o ./output \
  --format-version 1.0 \
  --enable-lod --enable-simplify --draco \
  --texture ktx2
```

## LOD × 简化 × Draco 联动矩阵

| LOD | simplify | draco | 行为 |
|-----|----------|-------|------|
| ON | ON | ON | **情况A**：多级自适应简化，LOD0 不压缩，LOD1+ Draco |
| ON | OFF | ON | **情况B**：多级结构不简化，LOD0 不压缩，LOD1+ Draco |
| ON | ON | OFF | 多级简化，无压缩 |
| OFF | ON | ON | 单级简化 + 全局 Draco |
| OFF | OFF | ON | 标准转换 + 全局 Draco |
| OFF | OFF | OFF | 标准转换 |

推荐组合 `--enable-lod --enable-simplify --draco` 下各级别：

| LOD 级别 | 简化率 | Draco | 3D Tiles 位置 |
|----------|--------|-------|---------------|
| LOD0 | 100% | 否 | Leaf（近景，最高精度） |
| LOD1 | 50% | 是 | Mid |
| LOD2 | 25% | 是 | Root（远景，最低精度） |

## 测试

```bash
./test.sh -v

# 或手动
source venv/bin/activate
PYTHONPATH=. pytest tests/ -v
```

## 项目结构

```
osgb2tiles/
├── __init__.py          # 版本号
├── __main__.py          # python -m 入口
├── cli.py               # argparse CLI + 参数联动状态打印
├── config.py            # ConvertConfig 配置数据类
├── metadata.py          # metadata.xml 解析 + CRS → ECEF 坐标变换
├── structure.py         # 目录结构自动检测（ContextCapture / DJI Terra）
├── osgb_parser.py       # OSGB 二进制解析 + osgconv 后端（DJI 格式）
├── obj_parser.py        # OBJ 格式解析器（osgconv 输出）
├── mesh_simplifier.py   # meshoptimizer 网格简化
├── gltf_assembler.py    # glTF 2.0 / GLB 组装（多材质 + Draco）
├── b3dm.py              # 3D Tiles 1.0 b3dm 打包
├── texture.py           # 纹理加载、缩放、格式编码
└── tileset_builder.py   # tileset.json 构建 + LOD 树 + 版本切换
tests/
├── test_core.py         # 核心模块测试
├── test_metadata.py     # 元数据解析测试
└── test_structure.py    # 目录结构检测测试
Makefile                 # make clean / test / run
run.sh                   # 一键运行（自动清缓存 + venv）
test.sh                  # 一键测试
ERRORFIX.md              # 问题修复记录
```

## 可选依赖

| 功能 | 依赖 | 安装方式 |
|------|------|----------|
| DJI OSGB 解析 | OpenSceneGraph | `apt install openscenegraph`（提供 `osgconv`） |
| 网格简化 | meshoptimizer | `pip install meshoptimizer`（已在 requirements.txt） |
| KTX2 纹理 | toktx | [KTX-Software](https://github.com/KhronosGroup/KTX-Software) |
| Draco 压缩 | draco | `pip install draco` |

## 技术细节

- **坐标变换**：pyproj 将 `metadata.xml` 中的 SRS（如 CGCS2000 EPSG:4546）→ EPSG:4326 → ECEF（EPSG:4978），ECEF 矩阵以列优先存储
- **Z-up → Y-up**：OSGB 使用 Z-up，glTF 使用 Y-up，顶点/法线变换为 `[x,y,z] → [x,z,-y]`
- **DJI OSGB 解析**：通过 `osgconv` 转换为 OBJ，嵌入 JPEG 纹理通过二进制搜索提取
- **多材质支持**：单瓦片可含多个材质组（`material_1` ~ `material_6`），每个材质独立纹理
- **b3dm 封装**：28 字节头部 + Feature Table + glb 载荷，整体 8 字节对齐

## 许可

本项目仅供学习和研究使用。

# OSGB2Tiles

OSGB 倾斜摄影数据 → 3D Tiles 转换工具。支持 3D Tiles 1.0 (b3dm) 与 1.1 (glb) 双版本输出，可直接在 Cesium 等三维地球引擎中加载。

## 功能特性

- **多源数据支持**：ContextCapture（含分幅 `Tile_+xxx_+xxx` 目录）和 DJI Terra OSGB 格式自动识别
- **双版本输出**：`--format-version 1.0` 输出 b3dm（向下兼容），`1.1` 输出 glb（默认）
- **多级 LOD**：`--enable-lod` 生成倒置树结构的多级细节层级
- **网格简化**：`--enable-simplify` 基于 meshoptimizer 的自适应三角形简化
- **条件 Draco 压缩**：LOD 模式下 LOD0 不压缩（保精度），LOD1+ 自动 Draco 压缩
- **空间四叉树重构**：`--enable-lod` 自动触发，将大量零散顶层瓦片归类到四叉树，解决 DJI Terra 等平铺结构的 children 堆积问题
- **多工程合并**：`merge` 子命令，纯文本级秒级合并多个独立 3D Tiles 工程为统一入口
- **多材质纹理**：单瓦片多材质组自动匹配嵌入 JPEG 纹理
- **坐标变换**：pyproj CRS → EPSG:4326 → ECEF，子午线收敛角自动校正，大地水准面高程纠正
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
| `--enable-lod` | 关闭 | 启用多级细节（LOD），同时自动触发空间四叉树重构 |
| `--enable-simplify` | 关闭 | 启用网格简化（需 meshoptimizer） |
| `--lod-levels` | `1.0,0.5,0.25` | LOD 级别比例，逗号分隔 |
| `--simplify-error` | `0.01` | 简化最大允许误差 |
| `--unlit` / `--no-unlit` | 开启 | 无光照着色 |
| `--back-face-culling` / `--no-back-face-culling` | 开启 | 背面裁切 |
| `--double-sided` | 关闭 | 强制双面渲染（与背面裁切互斥） |
| `--refine` | `REPLACE` | 细化模式：`ADD` / `REPLACE` |
| `--error-scale` | `1.0` | 几何误差缩放因子 |
| `--threads` | `8` | 并行线程数 |
| `--precise-coords` | 关闭 | 逐顶点坐标纠正（大范围场景精度更高） |
| `--clean` | 关闭 | 运行前清除 `__pycache__` 等编译产物（不重装依赖） |

### 多工程合并命令

```
python -m osgb2tiles merge -i <工程目录1> <工程目录2> ... -o <输出tileset.json>
```

| 参数 | 说明 |
|------|------|
| `-i`, `--input` | 子工程目录列表（每个目录需含 tileset.json） |
| `-o`, `--output` | 输出的总览 tileset.json 路径 |

## 使用示例

```bash
# 基本转换
./run.sh -i ./Data -o ./output

# 3D Tiles 1.0 b3dm 格式输出
./run.sh -i ./Data -o ./output --format-version 1.0

# LOD + 简化 + Draco 联动（自动触发四叉树重构）
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

# 多工程合并
python -m osgb2tiles merge -i ./region_A ./region_B ./region_C -o ./merged/tileset.json
```

## 两阶段（Two-Phase）优化策略

### 阶段一：空间四叉树重构（切片时自动触发）

当 `--enable-lod` 且根节点 children 数超过 4 时自动激活：

1. 收集所有顶层叶子瓦片的 ECEF 包围盒
2. 在 WGS84 经纬度空间构建四叉树（叶子阈值=4）
3. 自底向上合并子节点网格，通过 meshoptimizer 大幅抽稀（10% 保留率）
4. 生成宏观大瓦片作为新的顶层节点，根节点 children 数从数百降至 4-16

**解决的问题**：DJI Terra 等平铺结构导致 tileset.json 第一层 children 堆积成百上千个，引发 Cesium 并发请求卡死与"满天星"现象。

### 阶段二：多工程虚拟缝合（独立命令）

```bash
python -m osgb2tiles merge -i ./proj_A ./proj_B -o ./merged/tileset.json
```

- 纯文本级操作，不加载任何网格/纹理数据，秒级完成
- 读取各子工程 tileset.json，计算全局最小外接包围盒
- 生成总览 tileset.json，children 直接级联引用各子工程相对路径

**解决的问题**：多个区域数据集在 Cesium 中同时加载时，缓存分配互相抢占，导致看不见的块无法及时淘汰。

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
├── __init__.py                # 版本号
├── __main__.py                # python -m 入口（支持 merge 子命令）
├── cli.py                     # argparse CLI + 参数联动状态打印
├── config.py                  # ConvertConfig 配置数据类
├── metadata.py                # metadata.xml 解析 + CRS → ECEF 坐标变换
├── structure.py               # 目录结构自动检测（ContextCapture / DJI Terra）
├── osgb_parser.py             # OSGB 二进制解析 + osgconv 后端（DJI 格式）
├── obj_parser.py              # OBJ 格式解析器（osgconv 输出）
├── mesh_simplifier.py         # meshoptimizer 网格简化
├── gltf_assembler.py          # glTF 2.0 / GLB 组装（多材质 + Draco）
├── b3dm.py                    # 3D Tiles 1.0 b3dm 打包
├── texture.py                 # 纹理加载、缩放、格式编码
├── tileset_builder.py         # tileset.json 构建 + LOD 树 + 版本切换 + 四叉树重构接入
├── spatial_quadtree.py        # 空间四叉树数据结构与索引构建
├── top_level_reconstructor.py # 阶段一：顶层网格合并 + 抽稀 + 重构
└── merge_tool.py              # 阶段二：多工程 tileset.json 虚拟缝合
tests/
├── test_core.py               # 核心模块测试
├── test_metadata.py           # 元数据解析测试
├── test_structure.py          # 目录结构检测测试
├── test_spatial_quadtree.py   # 四叉树 + 网格合并测试 (22 个)
└── test_merge_tool.py         # 合并工具测试 (6 个)
Makefile                       # make clean / test / run
run.sh                         # 一键运行（自动清缓存 + venv）
test.sh                        # 一键测试
ERRORFIX.md                    # 问题修复记录
```

## 可选依赖

| 功能 | 依赖 | 安装方式 |
|------|------|----------|
| DJI OSGB 解析 | OpenSceneGraph | `apt install openscenegraph`（提供 `osgconv`） |
| 网格简化 | meshoptimizer | `pip install meshoptimizer`（已在 requirements.txt） |
| KTX2 纹理 | toktx | [KTX-Software](https://github.com/KhronosGroup/KTX-Software) |
| Draco 压缩 | DracoPy | `pip install DracoPy` |

## 技术细节

- **坐标变换**：pyproj 将 `metadata.xml` 中的 SRS（如 CGCS2000 EPSG:4546）→ EPSG:4326 → ECEF（EPSG:4978），自动校正子午线收敛角（网格北 vs 真北），EGM96 大地水准面高程纠正，ECEF 矩阵以列优先存储
- **顶点坐标系**：OSGB 顶点保持 Z-up 原生坐标系，通过 ECEF 变换矩阵直接定位
- **DJI OSGB 解析**：通过 `osgconv` 转换为 OBJ，嵌入 JPEG 纹理通过二进制搜索提取
- **多材质支持**：单瓦片可含多个材质组（`material_1` ~ `material_6`），每个材质独立纹理
- **b3dm 封装**：28 字节头部 + Feature Table + glb 载荷，整体 8 字节对齐
- **四叉树空间索引**：基于 WGS84 经纬度划分，叶子阈值 4 个瓦片，自底向上合并重构

## 许可

本项目仅供学习和研究使用。

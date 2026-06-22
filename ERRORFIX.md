# ERRORFIX.md — 问题修复记录

## 1. ECEF 变换矩阵存储顺序错误 — 瓦片位置偏移到南极

**现象**：Cesium 加载 3D Tiles 后，模型出现在南极附近（经度 103.7°, 纬度 -63.3°, 高程 -6361km），而非正确的台湾桃园位置。

**根因**：`tileset_builder.py` 中使用 `self.ecef_matrix.flatten()` 将 4×4 变换矩阵展平为一维数组。numpy 的 `flatten()` 默认使用**行优先**（row-major）顺序，而 3D Tiles/glTF 规范要求**列优先**（column-major）顺序。矩阵被转置存储后，Cesium 读到了完全错误的 ECEF 坐标。

**修复**：`tileset_builder.py:107`

```python
# 修复前
tile["transform"] = self.ecef_matrix.flatten().tolist()

# 修复后
tile["transform"] = self.ecef_matrix.T.flatten().tolist()
```

**验证**：修复后瓦片正确定位到 121.3°E, 25.0°N（台湾桃园）。

---

## 2. OSGB Z-up 到 glTF Y-up 坐标系未转换 — 模型方向错误

**现象**：模型在 Cesium 中显示时方向不对，需要手动将垂直轴从 Z 轴变换到 Y 轴才能正常显示。

**根因**：OSGB 使用 Z-up 坐标系（Z 轴朝上），而 glTF 使用 Y-up 坐标系（Y 轴朝上）。从 osgconv 输出的 OBJ 顶点数据未经坐标系转换。

**修复**：`osgb_parser.py` 的 `_extract_dji_meshes_via_osgconv()` 方法中，对顶点和法线应用坐标变换 `[x, y, z] → [x, z, -y]`：

```python
# 顶点坐标交换
vertices = obj_mesh.vertices.copy()
vertices[:, [1, 2]] = vertices[:, [2, 1]]  # 交换 Y 和 Z
vertices[:, 2] = -vertices[:, 2]           # 取反新 Z（原 Y）

# 法线同样交换
normals = obj_mesh.normals.copy()
normals[:, [1, 2]] = normals[:, [2, 1]]
normals[:, 2] = -normals[:, 2]
```

**注意**：初次实现时错误地取反了 Y 轴（`vertices[:, 1] = -vertices[:, 1]`），导致模型上下翻转。正确做法是取反新 Z 轴（原 Y 轴）。

---

## 3. 材质缺少 `baseColorTexture` 引用 — 无材质贴图

**现象**：生成的 GLB 文件中虽然包含纹理图片数据，但模型显示为纯色（无纹理）。

**根因**：`gltf_assembler.py` 的 `_build_material()` 方法创建材质时只设置了 `pbrMetallicRoughness` 的 `metallicFactor` 和 `roughnessFactor`，没有添加 `baseColorTexture` 引用。虽然 `_process_textures()` 方法将图片数据写入了 buffer 并添加了 images/textures 节点，但从未将纹理索引关联到材质。

**修复**：在 `_attach_texture()` 方法中，写入纹理数据后显式设置材质的 `baseColorTexture`：

```python
gltf["materials"][material_idx]["pbrMetallicRoughness"]["baseColorTexture"] = {
    "index": tex_index,
    "texCoord": 0,
}
```

---

## 4. 单瓦片多材质只处理一张纹理 — 纹理紊乱/马赛克

**现象**：模型纹理出现大面积错乱、马赛克碎块和黑色区域。建筑物外墙和地面纹理杂乱无章。

**根因**：DJI Terra 生成的 OSGB 文件中，每个叶节点瓦片可包含多个材质组（如 `material_1` 到 `material_6`），每个材质对应不同的嵌入 JPEG 纹理。但原代码的 `_process_textures()` 方法在处理第一张纹理后就 `break` 退出，导致所有 6 个材质组都使用同一张纹理。

**修复**：

1. **`obj_parser.py`**：为 `ObjMesh` 数据类添加 `material_name` 字段，在解析 OBJ 文件时记录每个网格对应的材质名。

2. **`osgb_parser.py`**：添加 `_match_texture_index()` 方法，通过材质名（`material_N`）匹配嵌入纹理索引（`N-1`）：

   ```python
   @staticmethod
   def _match_texture_index(material_name: Optional[str]) -> Optional[int]:
       if not material_name:
           return None
       m = re.match(r"material_(\d+)", material_name)
       if m:
           return int(m.group(1)) - 1
       return None
   ```

3. **`gltf_assembler.py`**：重构 `build_gltf()` 方法，为每个网格创建独立的材质、纹理和图片，并将每个 primitive 关联到对应的材质索引。将原来的 `_process_textures()` 替换为 `_attach_texture()` 方法，处理单个网格的纹理关联。

**验证**：6 个网格 → 6 个材质 → 6 张纹理 → 6 个 primitive，所有索引在顶点范围内，buffer view 无重叠。

---

## 5. osgconv 输出无效统一法线 — 法线全部为 (0,0,1)

**现象**：osgconv 将 DJI OSGB 转换为 OBJ 时，输出的法线数据全部为 `(0, 0, 1)`（统一指向 Z 轴正方向），不包含实际的表面朝向信息。这导致光照计算错误，部分表面显示为黑色。

**根因**：`osgb_parser.py` 中使用 `np.all(obj_mesh.normals == 0)` 检测是否需要重算法线。由于 osgconv 输出的法线是 `(0, 0, 1)` 而非全零，检测条件不满足，代码直接使用了这些无效法线。

**修复**：增加对统一法线的检测，当所有法线值相同时也触发重算：

```python
need_recompute = (
    np.all(obj_mesh.normals == 0)
    or len(obj_mesh.normals) == 0
    or np.allclose(obj_mesh.normals, obj_mesh.normals[0], atol=1e-6)
)
if need_recompute and len(obj_mesh.indices) > 0:
    obj_mesh.normals = compute_normals(obj_mesh.vertices, obj_mesh.indices)
```

---

## 6. OBJ V 坐标未翻转 — 纹理上下颠倒

**现象**：纹理在模型上显示为上下颠倒或错位。

**根因**：OBJ 格式的纹理坐标 V 轴原点在**左下角**（底部），而 glTF 格式的 V 轴原点在**左上角**（顶部）。osgconv 输出的 OBJ 文件使用 OBJ 标准的 V 坐标方向，直接用于 glTF 会导致纹理垂直翻转。

**修复**：在 `osgb_parser.py` 的坐标转换阶段添加 V 坐标翻转：

```python
uvs = obj_mesh.uvs.copy()
uvs[:, 1] = 1.0 - uvs[:, 1]
```

---

## 7. 纹理被 PIL 重复编码 — 质量损失

**现象**：嵌入的 JPEG 纹理经过 PIL 重新编码后出现压缩画质损失。

**根因**：`gltf_assembler.py` 的 `_attach_texture()` 方法对所有纹理数据调用 `resize_texture()` + `encode_texture()`，即使原始数据已经是 JPEG 格式且尺寸在限制范围内，也会被 PIL 解码后重新编码为 JPEG，引入额外的压缩损失。

**修复**：添加 JPEG 格式检测和尺寸检查，当原始数据已是目标格式且未超限时直接复用：

```python
if self.config.texture_format == TextureFormat.JPG and self._is_jpeg(raw):
    if self._jpeg_within_size(raw, self.config.max_texture_size):
        encoded = raw  # 直接复用，不重新编码
    else:
        encoded = encode_texture(resize_texture(raw, ...), ...)
else:
    encoded = encode_texture(resize_texture(raw, ...), ...)
```

---

## 附录：修改的文件清单

| 文件 | 修改内容 |
|------|----------|
| `tileset_builder.py` | ECEF 矩阵 `.T.flatten()` 列优先存储；添加进度打印 |
| `osgb_parser.py` | Z-up→Y-up 坐标交换；嵌入纹理提取；多纹理按材质名匹配；统一法线检测；V 坐标翻转 |
| `gltf_assembler.py` | 多材质支持；`_attach_texture()` 替代 `_process_textures()`；`baseColorTexture` 显式关联；JPEG 直通优化 |
| `obj_parser.py` | `ObjMesh` 添加 `material_name` 字段 |
| `Makefile` | 新增，提供 `make clean/test/run` 工作流 |
| `run.sh` | 每次运行前自动清除 `__pycache__` |

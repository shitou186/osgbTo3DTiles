"""OBJ 格式解析器。

解析 Wavefront OBJ 文件，提取顶点、纹理坐标和面数据。
用于处理 osgconv 转换 DJI Terra OSGB 文件后的输出。
"""

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np


@dataclass
class ObjMesh:
    """OBJ 网格数据。"""
    vertices: np.ndarray    # (N, 3) float32
    normals: np.ndarray     # (N, 3) float32
    uvs: np.ndarray         # (N, 2) float32
    indices: np.ndarray     # (M,) uint32
    texture_path: Optional[str] = None
    material_name: Optional[str] = None


@dataclass
class ObjModel:
    """OBJ 模型数据。"""
    meshes: List[ObjMesh] = field(default_factory=list)
    mtl_lib: Optional[str] = None
    mtl_textures: dict = field(default_factory=dict)


class ObjParser:
    """OBJ 文件解析器。"""

    def parse_file(self, obj_path: str) -> ObjModel:
        """解析 OBJ 文件。"""
        model = ObjModel()
        
        with open(obj_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        # 临时存储原始数据
        positions = []
        texcoords = []
        normals = []
        faces = []
        current_mtl = None
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            
            parts = line.split()
            if not parts:
                continue
            
            cmd = parts[0]
            
            if cmd == "mtllib":
                model.mtl_lib = parts[1]
                self._load_mtl(obj_path, model)
            
            elif cmd == "usemtl":
                current_mtl = parts[1]
            
            elif cmd == "v":
                positions.append([float(parts[1]), float(parts[2]), float(parts[3])])
            
            elif cmd == "vt":
                texcoords.append([float(parts[1]), float(parts[2])])
            
            elif cmd == "vn":
                normals.append([float(parts[1]), float(parts[2]), float(parts[3])])
            
            elif cmd == "f":
                face_verts = []
                face_uvs = []
                face_normals = []
                
                for part in parts[1:]:
                    indices = part.split("/")
                    v_idx = int(indices[0]) - 1
                    face_verts.append(v_idx)
                    
                    if len(indices) > 1 and indices[1]:
                        vt_idx = int(indices[1]) - 1
                        face_uvs.append(vt_idx)
                    
                    if len(indices) > 2 and indices[2]:
                        vn_idx = int(indices[2]) - 1
                        face_normals.append(vn_idx)
                
                # 三角化面（假设是凸多边形）
                for i in range(1, len(face_verts) - 1):
                    faces.append({
                        "verts": [face_verts[0], face_verts[i], face_verts[i + 1]],
                        "uvs": [face_uvs[0], face_uvs[i], face_uvs[i + 1]] if face_uvs else [],
                        "normals": [face_normals[0], face_normals[i], face_normals[i + 1]] if face_normals else [],
                        "mtl": current_mtl,
                    })
        
        if not positions:
            return model
        
        # 构建网格
        positions_arr = np.array(positions, dtype=np.float32)
        texcoords_arr = np.array(texcoords, dtype=np.float32) if texcoords else np.zeros((len(positions), 2), dtype=np.float32)
        normals_arr = np.array(normals, dtype=np.float32) if normals else np.zeros((len(positions), 3), dtype=np.float32)
        
        # 按材质分组
        mtl_groups = {}
        for face in faces:
            mtl = face.get("mtl")
            if mtl not in mtl_groups:
                mtl_groups[mtl] = []
            mtl_groups[mtl].append(face)
        
        for mtl, mtl_faces in mtl_groups.items():
            # 构建顶点索引映射（去重）
            vertex_map = {}
            new_vertices = []
            new_texcoords = []
            new_normals = []
            new_indices = []
            
            for face in mtl_faces:
                for i, v_idx in enumerate(face["verts"]):
                    key = (v_idx,)
                    if face["uvs"]:
                        key = (v_idx, face["uvs"][i])
                    if face["normals"]:
                        key = (v_idx, face["uvs"][i] if face["uvs"] else -1, face["normals"][i])
                    
                    if key not in vertex_map:
                        vertex_map[key] = len(new_vertices)
                        new_vertices.append(positions_arr[v_idx])
                        
                        if face["uvs"] and face["uvs"][i] < len(texcoords_arr):
                            new_texcoords.append(texcoords_arr[face["uvs"][i]])
                        else:
                            new_texcoords.append([0.0, 0.0])
                        
                        if face["normals"] and face["normals"][i] < len(normals_arr):
                            new_normals.append(normals_arr[face["normals"][i]])
                        else:
                            new_normals.append([0.0, 0.0, 1.0])
                    
                    new_indices.append(vertex_map[key])
            
            if not new_vertices:
                continue
            
            mesh = ObjMesh(
                vertices=np.array(new_vertices, dtype=np.float32),
                normals=np.array(new_normals, dtype=np.float32),
                uvs=np.array(new_texcoords, dtype=np.float32),
                indices=np.array(new_indices, dtype=np.uint32),
                material_name=mtl,
            )
            
            # 查找纹理路径
            if mtl and mtl in model.mtl_textures:
                tex_path = model.mtl_textures[mtl]
                obj_dir = os.path.dirname(obj_path)
                full_path = os.path.join(obj_dir, tex_path)
                if os.path.exists(full_path):
                    mesh.texture_path = full_path
            
            model.meshes.append(mesh)
        
        # 如果没有面但有顶点，创建一个空网格
        if not model.meshes and len(positions) > 0:
            mesh = ObjMesh(
                vertices=positions_arr,
                normals=np.zeros((len(positions), 3), dtype=np.float32),
                uvs=np.zeros((len(positions), 2), dtype=np.float32),
                indices=np.array([], dtype=np.uint32),
            )
            model.meshes.append(mesh)
        
        return model
    
    def _load_mtl(self, obj_path: str, model: ObjModel):
        """加载 MTL 材质文件。"""
        obj_dir = os.path.dirname(obj_path)
        mtl_path = os.path.join(obj_dir, model.mtl_lib)
        
        if not os.path.exists(mtl_path):
            return
        
        current_mtl = None
        with open(mtl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                
                parts = line.split()
                if not parts:
                    continue
                
                if parts[0] == "newmtl":
                    current_mtl = parts[1]
                elif parts[0] == "map_Kd" and current_mtl:
                    # 纹理路径可能包含空格
                    tex_path = " ".join(parts[1:])
                    model.mtl_textures[current_mtl] = tex_path


def compute_normals(vertices: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """计算顶点法线。"""
    normals = np.zeros_like(vertices)
    
    for i in range(0, len(indices), 3):
        v0 = vertices[indices[i]]
        v1 = vertices[indices[i + 1]]
        v2 = vertices[indices[i + 2]]
        
        edge1 = v1 - v0
        edge2 = v2 - v0
        normal = np.cross(edge1, edge2)
        
        normals[indices[i]] += normal
        normals[indices[i + 1]] += normal
        normals[indices[i + 2]] += normal
    
    # 归一化
    norms = np.linalg.norm(normals, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normals = normals / norms
    
    return normals

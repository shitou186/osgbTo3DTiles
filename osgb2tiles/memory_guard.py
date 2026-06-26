"""内存管理守卫：numpy 缓冲区释放、gc 强制回收、临时文件追踪清理。"""

import gc
import os
import shutil
from typing import List

import numpy as np


class MemoryGuard:
    """追踪并清理临时文件和大内存分配。"""

    def __init__(self):
        self._temp_files: List[str] = []
        self._temp_dirs: List[str] = []

    def track_temp_file(self, path: str):
        """注册临时文件，析构时自动删除。"""
        self._temp_files.append(path)

    def track_temp_dir(self, path: str):
        """注册临时目录，析构时自动删除。"""
        self._temp_dirs.append(path)

    def cleanup(self):
        """立即清理所有追踪的临时文件。"""
        for f in self._temp_files:
            try:
                if os.path.exists(f):
                    os.unlink(f)
            except OSError:
                pass
        self._temp_files.clear()

        for d in self._temp_dirs:
            try:
                if os.path.exists(d):
                    shutil.rmtree(d)
            except OSError:
                pass
        self._temp_dirs.clear()

        gc.collect()

    def __del__(self):
        self.cleanup()


def release_numpy_refs(obj):
    """递归释放对象中的 numpy 数组引用。

    用于 OsgeMesh 列表的主动内存释放，将 ndarray 属性设为 None。
    """
    if isinstance(obj, np.ndarray):
        return
    if isinstance(obj, list):
        for item in obj:
            release_numpy_refs(item)
    elif hasattr(obj, '__dict__'):
        for key, val in obj.__dict__.items():
            if isinstance(val, np.ndarray):
                setattr(obj, key, None)
            elif isinstance(val, list):
                release_numpy_refs(val)


def force_gc():
    """强制三代垃圾回收，释放循环引用。"""
    gc.collect(0)
    gc.collect(1)
    gc.collect(2)

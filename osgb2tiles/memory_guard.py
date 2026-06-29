"""内存管理工具：numpy 缓冲区释放。"""

import numpy as np


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

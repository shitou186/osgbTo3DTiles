#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

# 每次运行前清除 __pycache__，确保使用最新代码
find "$SCRIPT_DIR/osgb2tiles" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# 自动创建虚拟环境并安装依赖
if [ ! -d "$VENV_DIR" ]; then
    echo "[setup] 创建虚拟环境..."
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
    echo "[setup] 依赖安装完成"
fi

export PYTHONPATH="$SCRIPT_DIR"
exec "$VENV_DIR/bin/python" -m osgb2tiles "$@"

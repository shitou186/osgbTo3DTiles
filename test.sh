#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "[setup] 创建虚拟环境并安装依赖..."
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
    "$VENV_DIR/bin/pip" install -q pytest
fi

export PYTHONPATH="$SCRIPT_DIR"
exec "$VENV_DIR/bin/python" -m pytest "$@"

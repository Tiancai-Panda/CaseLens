#!/usr/bin/env bash
set -Eeuo pipefail

# Copy the Windows checkout into the Linux filesystem and create a Linux-only
# environment. The Windows checkout, its .venv and its state directory remain
# untouched.

SOURCE_DIR="/mnt/c/Users/20824/Desktop/学习文件夹/caselens"
TARGET_DIR="${HOME}/projects/caselens"

if [[ ! -d "${SOURCE_DIR}" ]]; then
  echo "找不到 Windows 项目目录：${SOURCE_DIR}" >&2
  exit 1
fi

mkdir -p "${HOME}/projects"
mkdir -p "${TARGET_DIR}"

echo "正在复制项目到：${TARGET_DIR}"
tar \
  --exclude='.venv' \
  --exclude='.verify-venv' \
  --exclude='.caselens' \
  --exclude='.caselens-linux' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='.ruff_cache' \
  --exclude='build' \
  --exclude='dist' \
  -C "${SOURCE_DIR}" -cf - . | tar -C "${TARGET_DIR}" -xf -

cd "${TARGET_DIR}"

if [[ -f caselens.json ]]; then
  cp caselens.json caselens.wsl.json
  # Use a separate state directory so Windows and WSL runs do not share paths
  # or SQLite files. Keep all other provider settings and local API keys intact.
  sed -i -E 's/("state_dir"[[:space:]]*:[[:space:]]*)"[^"]*"/\1".caselens-linux"/' caselens.wsl.json
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3，请先执行：sudo apt update && sudo apt install -y python3 python3-venv python3-pip" >&2
  exit 1
fi

python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip

if ! ./.venv/bin/python -m pip install -e '.[dev]'; then
  ./.venv/bin/python -m pip install -e .
fi

echo
echo "Linux 环境已创建：${TARGET_DIR}/.venv"
echo "WSL 配置文件：${TARGET_DIR}/caselens.wsl.json"
echo
echo "开始运行配置检查（不会打印 API 密钥）："
./.venv/bin/caselens doctor --config ./caselens.wsl.json

if [[ "${CASE_LENS_SKIP_TESTS:-0}" != "1" ]]; then
  echo
  echo "开始运行自动化测试："
  if ./.venv/bin/python -c "import pytest" >/dev/null 2>&1; then
    ./.venv/bin/python -m pytest -q
  else
    echo "当前安装没有 pytest，已跳过测试；如需运行，请安装开发依赖后执行：.venv/bin/python -m pytest -q"
  fi
fi

echo
echo "迁移完成。启动 Web 工作台："
echo "cd '${TARGET_DIR}'"
echo "./.venv/bin/python -m uvicorn caselens.web:app --host 127.0.0.1 --port 8765"

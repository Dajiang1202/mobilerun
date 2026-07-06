#!/bin/bash
# OCR 服务启动脚本 — 设置 CUDA 库路径后启动
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

source "$VENV_DIR/bin/activate"

# 设置 NVIDIA CUDA 库路径（pip 安装的 nvidia-* 包）
NVIDIA_LIB="$VENV_DIR/lib/python3.12/site-packages/nvidia"
export LD_LIBRARY_PATH="$NVIDIA_LIB/cudnn/lib:$NVIDIA_LIB/cublas/lib:$NVIDIA_LIB/cuda_runtime/lib:$NVIDIA_LIB/cuda_nvrtc/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

echo "LD_LIBRARY_PATH=$LD_LIBRARY_PATH"
cd "$SCRIPT_DIR"
python ocr_server.py

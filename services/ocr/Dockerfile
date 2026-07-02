# TFT OCR 服务 (RapidOCR PP-OCRv4 ONNX) — V100 / Ubuntu 16.04 生产部署
# CUDA 11.8 + cuDNN 8 = V100(Volta) 黄金组合; base 是 ubuntu22.04 隔离掉 host 16.04 的老 glibc
FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y python3.10 python3-pip libgl1 libglib2.0-0 \
    && ln -s /usr/bin/python3.10 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ocr_server.py gpu_config.yaml ./
# repo 里是 models_fp16/, 拷进容器时对齐成 config 引用的 models/
COPY models_fp16 ./models

EXPOSE 8089
# 4 worker; 代码已用 asyncio.to_thread, 每个 worker 内部并行, 足够 9 并发
CMD ["uvicorn", "ocr_server:app", "--host", "0.0.0.0", "--port", "8089", "--workers", "4"]

# OCR 微服务部署指南

## 概述

基于 **RapidOCR (PP-OCRv4 ONNX) + FastAPI** 的中文 OCR 在线识别微服务，为 GameAuto 金铲铲之战提供低延迟 GPU 推理。

| 属性 | 值 |
|------|-----|
| OCR 引擎 | RapidOCR (PP-OCRv4 ONNX Runtime) |
| Web 框架 | FastAPI + uvicorn |
| GPU 加速 | ONNX Runtime CUDA EP + cuDNN 9 |
| 监听端口 | 8089 |
| 部署环境 | WSL2 Ubuntu-24.04 |

### 性能指标（RTX 5090）

| 指标 | 结果 |
|------|------|
| 单图服务端延迟 | **8ms** |
| 单图端到端延迟 | ~11ms (P50) |
| GPU 推理时间 | <1ms |
| 角度分类 | 已跳过（游戏 UI 文字均为正向） |

## 环境要求

- **OS**: Windows 11 + WSL2 Ubuntu-24.04
- **GPU**: NVIDIA GPU（支持 CUDA 12+）
- **NVIDIA 驱动**: ≥ 535（WSL2 GPU passthrough）
- **Python**: 3.12（系统自带）

## 从零部署步骤

### 1. 创建虚拟环境

```bash
# 在 WSL 中执行
mkdir -p /mnt/d/gameauto/mobilerun/services/ocr
python3 -m venv /mnt/d/gameauto/mobilerun/services/ocr/.venv
source /mnt/d/gameauto/mobilerun/services/ocr/.venv/bin/activate
```

### 2. 安装依赖

```bash
# PaddlePaddle GPU（CUDA 12 版）— 从官方源下载 wheel
wget -c 'https://paddle-wheel.bj.bcebos.com/2.6.1/linux/linux-gpu-cuda12.0-cudnn8.9-mkl-gcc12.2-avx/paddlepaddle_gpu-2.6.1.post120-cp312-cp312-linux_x86_64.whl' \
  -O /tmp/paddlepaddle_gpu_cuda12.whl

mv /tmp/paddlepaddle_gpu_cuda12.whl /tmp/paddlepaddle_gpu-2.6.1.post120-cp312-cp312-linux_x86_64.whl
pip install --no-deps /tmp/paddlepaddle_gpu-2.6.1.post120-cp312-cp312-linux_x86_64.whl

# RapidOCR + FastAPI
pip install 'paddleocr>=2.8,<3.0' rapidocr-onnxruntime onnxruntime-gpu \
  fastapi uvicorn python-multipart \
  -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

# NVIDIA CUDA 运行时库（通过 pip 安装，供 onnxruntime-gpu 使用）
pip download nvidia-cudnn-cu12 nvidia-cuda-runtime-cu12 nvidia-cuda-nvrtc-cu12 nvidia-cublas-cu12 \
  --no-deps -d /tmp/nvidia_pkgs
pip install /tmp/nvidia_pkgs/*.whl --no-deps
```

> ⚠️ PaddlePaddle 2.6.x 的 GPU wheel 编译为 CUDA 12.0，WSL2 驱动需 ≥ 535。
> nvidia-cudnn-cu12 和 nvidia-cublas-cu12 包体较大（~700MB + ~580MB），网络不佳时用 `wget -c` 断点续传。

### 3. 验证 GPU 可用

```bash
source .venv/bin/activate
python3 -c "
import onnxruntime as ort
print('Providers:', ort.get_available_providers())
# 期望输出包含 CUDAExecutionProvider
"
```

如果 `CUDAExecutionProvider` 缺失，检查 `LD_LIBRARY_PATH` 是否包含 cuDNN 库路径。

### 4. 放置服务文件

将以下文件放入 `services/ocr/` 目录：

| 文件 | 说明 |
|------|------|
| `ocr_server.py` | FastAPI 服务主文件 |
| `gpu_config.yaml` | RapidOCR GPU 配置 |
| `start.sh` | 启动脚本（设置 CUDA 库路径） |

### 5. 启动服务

```bash
bash /mnt/d/gameauto/mobilerun/services/ocr/start.sh
```

首次启动会自动下载 PP-OCRv4 模型文件（~15MB）到 `~/.paddleocr/whl/`，并执行预热推理。

启动成功后输出：
```
INFO:     Uvicorn running on http://0.0.0.0:8089 (Press CTRL+C to quit)
```

### 6. 验证服务

```bash
# 健康检查
curl http://localhost:8089/health
# {"status":"ok","gpu":"NVIDIA GeForce RTX 5090 D","model":"PP-OCRv4 (ONNX)"}

# OCR 测试
curl -X POST http://localhost:8089/ocr \
  -H "Content-Type: application/json" \
  -d '{"image":"<base64_encoded_png>","lang":"ch"}'
```

## API 接口

### `POST /ocr` — 识别文字

**请求体（JSON）：**

```json
{
  "image": "<base64 编码的 PNG/JPEG>",
  "lang": "ch",
  "use_angle_cls": true,
  "threshold": 0.5
}
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `image` | string | (必填) | Base64 编码的图片 |
| `lang` | string | `"ch"` | 语言：`ch` 中文 / `en` 英文 |
| `use_angle_cls` | bool | `true` | 方向分类（当前配置已全局关闭） |
| `threshold` | float | `0.5` | 置信度过滤阈值 |

**响应体：**

```json
{
  "texts": ["娑娜"],
  "confidences": [0.96],
  "combined_text": "娑娜",
  "latency_ms": 8
}
```

### `POST /ocr_binary` — 二进制上传（略快）

Multipart form-data 直接上传图片文件，跳过 base64 编解码：

```bash
curl -X POST http://localhost:8089/ocr_binary \
  -F "image=@test.png" \
  -F "lang=ch" \
  -F "threshold=0.5"
```

> 对于小图（<10KB），base64 与二进制上传性能差异可忽略。

### `GET /health` — 健康检查

```json
{
  "status": "ok",
  "gpu": "NVIDIA GeForce RTX 5090 D",
  "model": "PP-OCRv4 (ONNX)"
}
```

## 配置说明

`gpu_config.yaml` 关键配置：

```yaml
Global:
    use_cls: false          # 跳过角度分类（游戏 UI 文字均正向）
    min_height: 30          # 最小文本高度（px）
    min_side_len: 30        # 最小边长

Det:
    use_cuda: true          # GPU 推理
    limit_side_len: 160     # 检测分辨率限制（越小越快，小图场景适用）
    thresh: 0.3             # 检测阈值
    box_thresh: 0.5         # 框置信度阈值

    intra_op_num_threads: 1 # 单线程减少切换开销
    inter_op_num_threads: 1

Rec:
    use_cuda: true
    rec_batch_num: 6        # 批量识别
```

### 性能调优参数

| 参数 | 当前值 | 说明 |
|------|--------|------|
| `limit_side_len` | 160 | 越小检测越快。小图（<200px）场景推荐 160；大图需调高到 320-736 |
| `use_cls` | false | 游戏 UI 文字均正向，跳过可省 ~10ms |
| `intra_op_num_threads` | 1 | 单请求用 1 线程，避免线程切换开销 |
| `thresh` | 0.3 | 游戏截图高对比度，可适当降低 |

## 客户端对接（Python）

```python
import base64, json, urllib.request

def ocr(image_bytes: bytes, host="localhost", port=8089, threshold=0.5):
    """调用 OCR 微服务识别图片中的文字。"""
    b64 = base64.b64encode(image_bytes).decode()
    data = json.dumps({"image": b64, "lang": "ch", "threshold": threshold}).encode()
    req = urllib.request.Request(
        f"http://{host}:{port}/ocr",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())

# 使用示例
with open("screenshot.png", "rb") as f:
    result = ocr(f.read())

print(result["texts"])         # ["娑娜", "嘉文四世"]
print(result["combined_text"]) # "娑娜 嘉文四世"
print(result["latency_ms"])    # 8
```

### 异步并发调用（一帧 10 个 ROI）

```python
import asyncio, aiohttp, base64, json

async def ocr_async(session, image_bytes, name):
    b64 = base64.b64encode(image_bytes).decode()
    async with session.post("http://localhost:8089/ocr",
                            json={"image": b64, "lang": "ch"}) as resp:
        result = await resp.json()
        return name, result

async def ocr_frame(rois: dict[str, bytes]):
    """rois: {"gold": bytes, "level": bytes, ...}"""
    async with aiohttp.ClientSession() as session:
        tasks = [ocr_async(session, img, name) for name, img in rois.items()]
        return dict(await asyncio.gather(*tasks))
```

## 后台运行

### 方式一：nohup

```bash
nohup bash /mnt/d/gameauto/mobilerun/services/ocr/start.sh > /tmp/ocr_server.log 2>&1 &
```

### 方式二：systemd service

创建 `/etc/systemd/system/ocr.service`：

```ini
[Unit]
Description=OCR Microservice
After=network.target

[Service]
Type=simple
User=dajiang1202
WorkingDirectory=/mnt/d/gameauto/mobilerun/services/ocr
Environment=LD_LIBRARY_PATH=/mnt/d/gameauto/mobilerun/services/ocr/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib:/mnt/d/gameauto/mobilerun/services/ocr/.venv/lib/python3.12/site-packages/nvidia/cublas/lib:/mnt/d/gameauto/mobilerun/services/ocr/.venv/lib/python3.12/site-packages/nvidia/cuda_runtime/lib:/mnt/d/gameauto/mobilerun/services/ocr/.venv/lib/python3.12/site-packages/nvidia/cuda_nvrtc/lib
ExecStart=/mnt/d/gameauto/mobilerun/services/ocr/.venv/bin/python /mnt/d/gameauto/mobilerun/services/ocr/ocr_server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable ocr
sudo systemctl start ocr
sudo systemctl status ocr
```

## 故障排查

| 问题 | 原因 | 解决 |
|------|------|------|
| `CUDAExecutionProvider is not available` | cuDNN 9 库未在 LD_LIBRARY_PATH 中 | 确认 `start.sh` 中 `LD_LIBRARY_PATH` 设置正确 |
| `Segmentation fault` | PaddlePaddle 不支持 GPU 架构 | 使用 RapidOCR (ONNX Runtime) 替代 PaddlePaddle 直接推理 |
| 首次请求慢（~300ms） | CUDA kernel 编译 + 模型预热 | 正常现象，后续请求 ~10ms |
| 识别结果为空 | `limit_side_len` 太小或图片无文字 | 调高 `limit_side_len` 或检查输入图片 |
| `libcudnn.so.9: cannot open` | 未安装 nvidia-cudnn-cu12 | `pip install nvidia-cudnn-cu12 --no-deps` |

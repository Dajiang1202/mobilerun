# OCR 微服务部署需求 — GameAuto 金铲铲之战

## 1. 业务场景

为游戏自动化框架提供 **中文 OCR 在线识别服务**。每帧截图裁剪出 ~10 个小区域，逐个发给 OCR 服务识别（并行请求），要求低延迟返回。

## 2. 输入图片规格

| 属性 | 值 |
|------|-----|
| 格式 | PNG 或 JPEG（base64 编码） |
| 原图分辨率 | ~720×1600（scrcpy scale=2 输出） |
| 实际发送 | **裁剪后的小图**，不是全图 |
| 单张小图尺寸 | 约 50×50 ~ 230×500 像素 |

### 每帧 ~10 个 OCR 请求（并行发出）

| 任务 | 内容 | 典型大小 | 识别目标 |
|------|------|----------|----------|
| gold | 金币数字 | ~60×100 | "42"（1-3位数字） |
| level | 等级 | ~40×50 | "LV.5"→"5" |
| hp | 血量 | ~90×50 | "78"（数字） |
| timer | 倒计时 | ~90×60 | "22"（数字） |
| shop_slot_0~4 | 商店棋子名 | ~120×250 | "娑娜"/"嘉文四世"（2-4个中文字） |
| bench | 备战席 | ~660×100 | 多个棋子名混合文本 |

### 图片特征

- 游戏 UI 截图，暗色背景（深蓝/黑/紫）
- 文字白色/金色/黄色，高对比度
- 中文棋子名使用游戏内特殊字体（类楷体）
- 数字区域字体清晰标准
- 部分区域有半透明 UI 覆盖

## 3. API 接口

```
POST /ocr
Content-Type: application/json
```

**请求体：**
```json
{
  "image": "<base64 encoded PNG/JPEG>",
  "lang": "ch",
  "use_angle_cls": true,
  "threshold": 0.5
}
```

**响应体：**
```json
{
  "texts": ["娑娜"],
  "confidences": [0.96],
  "combined_text": "娑娜",
  "latency_ms": 12
}
```

**健康检查：**
```
GET /health → {"status": "ok", "gpu": "RTX 5090", "model": "PP-OCRv4"}
```

## 4. 性能要求（RTX 5090）

| 指标 | 要求 | 说明 |
|------|------|------|
| 单次 OCR 延迟 | ≤ 30ms（p95） | 单张小图端到端（含网络） |
| 10 并发延迟 | ≤ 50ms（p95） | 一帧 10 个请求同时到达 |
| 吞吐量 | ≥ 200 QPS | 持续并发 |
| GPU 显存 | ≤ 4GB | PP-OCRv4 det+cls+rec |
| 首次加载 | ≤ 10s | 模型预热 |

### 5090 能力评估

RTX 5090（32GB VRAM）跑 PP-OCRv4 绰绰有余：
- 单图 det+rec 估算 ~5-8ms
- 10 并发 ~15-25ms（GPU batch inference）
- 瓶颈在网络传输（base64 编解码），非 GPU 推理

### 优化建议

1. **PP-OCRv4**（非 v3）— 中文准确率 +15%，速度 +30%
2. **TensorRT FP16** — 5090 支持，推理延迟减半
3. **图片预处理放客户端** — ROI 裁剪客户端完成，服务只收小图
4. **HTTP keep-alive** — 复用连接，避免 TCP 握手开销
5. **服务启动预热** — dummy inference 避免首帧延迟

## 5. 推荐技术栈

| 组件 | 推荐 | 备选 |
|------|------|------|
| OCR 引擎 | PaddleOCR PP-OCRv4 | RapidOCR |
| Web 框架 | FastAPI + uvicorn | Flask + gunicorn |
| GPU 加速 | PaddlePaddle GPU + TensorRT | ONNX Runtime + TRT |
| 部署 | Docker + NVIDIA Container Toolkit | 直接 pip |

## 6. Docker 部署参考

```dockerfile
FROM paddlepaddle/paddle:2.6.0-gpu-cuda12.0-cudnn8.9-trt8.6

RUN pip install paddleocr fastapi uvicorn python-multipart

COPY ocr_server.py /app/
WORKDIR /app

EXPOSE 8089
CMD ["uvicorn", "ocr_server:app", "--host", "0.0.0.0", "--port", "8089"]
```

## 7. 客户端对接

服务部署后，客户端只需改 `OcrTask` 调用方式：

- **原**: 本地 `PaddleOCR.ocr(img)` 同步调用
- **新**: `POST http://<ocr_host>:8089/ocr` 异步 HTTP
- **返回格式完全一致**: `{"texts": [...], "confidences": [...], "combined_text": "..."}`

"""OCR 微服务 — RapidOCR (PP-OCRv4 ONNX) + FastAPI

为 GameAuto 金铲铲之战提供中文 OCR 在线识别服务。
使用 RapidOCR (ONNX Runtime) 运行 PP-OCRv4 模型，兼容 RTX 5090。

Usage:
    source .venv/bin/activate
    uvicorn ocr_server:app --host 0.0.0.0 --port 8089
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import subprocess
import time
from contextlib import asynccontextmanager

import cv2
import numpy as np
from fastapi import FastAPI, UploadFile, File, Form
from pydantic import BaseModel, Field

logger = logging.getLogger("ocr_server")

# ── RapidOCR 单例 ──────────────────────────────────────────────────────
_ocr_instance = None
_gpu_name = "unknown"


def _get_ocr():
    """Lazy-init RapidOCR 单例。"""
    global _ocr_instance
    if _ocr_instance is None:
        import os
        from rapidocr_onnxruntime import RapidOCR

        config_path = os.path.join(os.path.dirname(__file__), "gpu_config.yaml")
        _ocr_instance = RapidOCR(config_path=config_path)
        logger.info("RapidOCR initialized (ONNX Runtime + CUDA, PP-OCRv4)")
    return _ocr_instance


def _detect_gpu():
    """检测 GPU 信息。"""
    global _gpu_name
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            _gpu_name = result.stdout.strip().split("\n")[0]
        else:
            _gpu_name = "CPU"
    except Exception:
        _gpu_name = "CPU"


def _warmup():
    """启动时 dummy inference 预热模型，避免首帧延迟。"""
    try:
        ocr = _get_ocr()
        dummy = np.random.randint(0, 255, (100, 200, 3), dtype=np.uint8)
        ocr(dummy)
        logger.info("Model warmup complete")
    except Exception:
        logger.exception("Model warmup failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动/关闭生命周期。"""
    _detect_gpu()
    _warmup()
    yield


app = FastAPI(title="OCR Service", version="1.0.0", lifespan=lifespan)


# ── 请求/响应模型 ──────────────────────────────────────────────────────


class OCRRequest(BaseModel):
    image: str = Field(description="Base64 编码的 PNG/JPEG 图片")
    lang: str = Field(default="ch", description="语言: ch / en")
    use_angle_cls: bool = Field(default=True, description="是否使用方向分类器")
    threshold: float = Field(default=0.5, description="置信度阈值")


class OCRResponse(BaseModel):
    texts: list[str]
    confidences: list[float]
    combined_text: str
    latency_ms: int


class HealthResponse(BaseModel):
    status: str
    gpu: str
    model: str


# ── API 路由 ───────────────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok", gpu=_gpu_name, model="PP-OCRv4 (ONNX)")


@app.post("/ocr", response_model=OCRResponse)
async def ocr(req: OCRRequest):
    t0 = time.perf_counter()

    try:
        # 解码 base64 → BGR numpy array
        img_bytes = base64.b64decode(req.image)
        img_array = np.frombuffer(img_bytes, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

        if img is None:
            return OCRResponse(
                texts=[],
                confidences=[],
                combined_text="",
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        # 运行 RapidOCR: result = [[box, text, confidence], ...] or None
        # 用 to_thread 丢进线程池 —— 否则同步推理阻塞 event loop, 并发请求被串行化。
        ocr_engine = _get_ocr()
        result, elapse = await asyncio.to_thread(ocr_engine, img)

        texts: list[str] = []
        confidences: list[float] = []

        if result:
            for item in result:
                # RapidOCR: [box, text, confidence]
                text = item[1]
                conf = float(item[2])
                if conf >= req.threshold and text.strip():
                    texts.append(text.strip())
                    confidences.append(conf)

        latency_ms = int((time.perf_counter() - t0) * 1000)
        return OCRResponse(
            texts=texts,
            confidences=confidences,
            combined_text=" ".join(texts),
            latency_ms=latency_ms,
        )

    except Exception:
        logger.exception("OCR failed")
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return OCRResponse(
            texts=[],
            confidences=[],
            combined_text="",
            latency_ms=latency_ms,
        )


@app.post("/ocr_binary", response_model=OCRResponse)
async def ocr_binary(
    image: UploadFile = File(...),
    lang: str = Form(default="ch"),
    use_angle_cls: bool = Form(default=True),
    threshold: float = Form(default=0.5),
):
    """二进制上传接口 — 避免 base64 编解码开销。"""
    t0 = time.perf_counter()

    try:
        img_bytes = await image.read()
        img_array = np.frombuffer(img_bytes, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

        if img is None:
            ms = int((time.perf_counter() - t0) * 1000)
            return OCRResponse(texts=[], confidences=[], combined_text="", latency_ms=ms)

        ocr_engine = _get_ocr()
        result, elapse = await asyncio.to_thread(ocr_engine, img)

        texts: list[str] = []
        confidences: list[float] = []

        if result:
            for item in result:
                text = item[1]
                conf = float(item[2])
                if conf >= threshold and text.strip():
                    texts.append(text.strip())
                    confidences.append(conf)

        ms = int((time.perf_counter() - t0) * 1000)
        return OCRResponse(
            texts=texts,
            confidences=confidences,
            combined_text=" ".join(texts),
            latency_ms=ms,
        )

    except Exception:
        logger.exception("OCR binary failed")
        ms = int((time.perf_counter() - t0) * 1000)
        return OCRResponse(texts=[], confidences=[], combined_text="", latency_ms=ms)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8089)

"""OCR 微服务 — 高性能版 (TensorRT EP + FP16, 跳过角度分类)

直接加载 PP-OCRv4 det/rec ONNX 模型，使用 TensorRT EP FP16 推理。
游戏 UI 文字均为正向，跳过角度分类模型，节省一次推理。

Usage:
    source .venv/bin/activate
    python ocr_server_opt.py
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI
from pydantic import BaseModel, Field

logger = logging.getLogger("ocr_server_opt")

# ── 模型路径 ────────────────────────────────────────────────────────────
_FP16_MODELS = Path(__file__).parent / "models_fp16"
_TRT_CACHE_DIR = Path(__file__).parent / "trt_cache"

# ── 全局模型 ────────────────────────────────────────────────────────────
_det_session = None
_rec_session = None
_gpu_name = "unknown"
_rec_char_list: list[str] = []


def _create_session(model_path: str, trt_cache_dir: str):
    """创建 ONNX Runtime session，优先 TensorRT EP FP16。"""
    import onnxruntime as ort

    sess_opt = ort.SessionOptions()
    sess_opt.log_severity_level = 4
    sess_opt.enable_cpu_mem_arena = False
    sess_opt.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess_opt.intra_op_num_threads = 1  # 单请求用 1 线程避免开销
    sess_opt.inter_op_num_threads = 1

    cuda_opts = {
        "device_id": 0,
        "arena_extend_strategy": "kSameAsRequested",
        "cudnn_conv_algo_search": "EXHAUSTIVE",
        "do_copy_in_default_stream": True,
    }

    providers = [
        ("CUDAExecutionProvider", cuda_opts),
        "CPUExecutionProvider",
    ]

    session = ort.InferenceSession(model_path, sess_options=sess_opt, providers=providers)
    actual_provider = session.get_providers()[0]
    logger.info("  %s → provider: %s", Path(model_path).name, actual_provider)
    return session


def _load_models():
    """加载 det + rec 模型（跳过 cls）。"""
    global _det_session, _rec_session, _rec_char_list

    _TRT_CACHE_DIR.mkdir(exist_ok=True)

    det_path = str(_FP16_MODELS / "ch_PP-OCRv4_det_infer.onnx")
    rec_path = str(_FP16_MODELS / "ch_PP-OCRv4_rec_infer.onnx")

    logger.info("Loading det model...")
    _det_session = _create_session(det_path, str(_TRT_CACHE_DIR))

    logger.info("Loading rec model...")
    _rec_session = _create_session(rec_path, str(_TRT_CACHE_DIR))

    # 读取 rec 模型的字符表
    meta = _rec_session.get_modelmeta().custom_metadata_map
    _rec_char_list = meta.get("character", "").splitlines()
    logger.info("Character list: %d chars", len(_rec_char_list))


def _detect_gpu():
    global _gpu_name
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            _gpu_name = result.stdout.strip().split("\n")[0]
    except Exception:
        _gpu_name = "CPU"


def _warmup():
    """预热：dummy inference 触发 TensorRT 编译。"""
    logger.info("Warming up (TensorRT compile + first inference)...")
    # det: 输入 [1, 3, H, W] float32
    dummy_det = np.random.rand(1, 3, 736, 736).astype(np.float32)
    # 获取 det 输入名
    det_input_name = _det_session.get_inputs()[0].name
    # 用一个小图做 det
    small = np.zeros((1, 3, 96, 96), dtype=np.float32)
    try:
        _det_session.run(None, {det_input_name: small})
    except Exception:
        pass  # 小图可能没有检测到文本，正常

    # rec: 输入 [1, 3, 48, W] float32
    rec_input_name = _rec_session.get_inputs()[0].name
    dummy_rec = np.random.rand(1, 3, 48, 320).astype(np.float32)
    _rec_session.run(None, {rec_input_name: dummy_rec})
    logger.info("Warmup complete")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _detect_gpu()
    _load_models()
    _warmup()
    yield


app = FastAPI(title="OCR Service (Optimized)", version="2.0.0", lifespan=lifespan)


# ── 检测后处理 ──────────────────────────────────────────────────────────

def _det_postprocess(pred_map: np.ndarray, thresh: float = 0.3,
                     box_thresh: float = 0.5, unclip_ratio: float = 1.6,
                     max_candidates: int = 100) -> list[np.ndarray]:
    """简化的检测后处理：从概率图提取文本框。"""
    import pyclipper

    h, w = pred_map.shape
    mask = (pred_map > thresh).astype(np.uint8)

    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours[:max_candidates]:
        if len(contour) < 4:
            continue
        epsilon = 0.002 * cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, epsilon, True)
        if len(approx) < 4:
            continue

        # Vatti clipping 膨胀
        points = approx.reshape(-1, 2)
        score = _box_score(pred_map, points)
        if score < box_thresh:
            continue

        # unclip
        poly = points.tolist()
        distance = cv2.contourArea(np.array(poly, dtype=np.float32)) * unclip_ratio / cv2.arcLength(np.array(poly, dtype=np.float32), True)
        offset = pyclipper.PyclipperOffset()
        offset.AddPath(poly, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
        expanded = offset.Execute(distance)
        if not expanded:
            continue

        box = np.array(expanded[0], dtype=np.float32)
        # 最小外接矩形
        rect = cv2.minAreaRect(box)
        box_pts = cv2.boxPoints(rect)
        box_pts = _order_points(box_pts)
        boxes.append(box_pts)

    return boxes


def _box_score(pred_map: np.ndarray, points: np.ndarray) -> float:
    """计算框内平均置信度。"""
    h, w = pred_map.shape
    xmin = np.clip(int(np.floor(points[:, 0].min())), 0, w - 1)
    xmax = np.clip(int(np.ceil(points[:, 0].max())), 0, w - 1)
    ymin = np.clip(int(np.floor(points[:, 1].min())), 0, h - 1)
    ymax = np.clip(int(np.ceil(points[:, 1].max())), 0, h - 1)
    mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
    shifted = points - np.array([xmin, ymin])
    cv2.fillPoly(mask, [shifted.astype(np.int32)], 1)
    return pred_map[ymin:ymax + 1, xmin:xmax + 1][mask == 1].mean()


def _order_points(pts: np.ndarray) -> np.ndarray:
    """排序为 [左上, 右上, 右下, 左下]。"""
    ordered = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    ordered[0] = pts[np.argmin(s)]  # 左上
    ordered[2] = pts[np.argmax(s)]  # 右下
    d = np.diff(pts, axis=1)
    ordered[1] = pts[np.argmin(d)]  # 右上
    ordered[3] = pts[np.argmax(d)]  # 左下
    return ordered


def _get_crop(img: np.ndarray, box: np.ndarray) -> np.ndarray:
    """根据四点框裁剪并透视变换。"""
    w = int(np.linalg.norm(box[1] - box[0]))
    h = int(np.linalg.norm(box[3] - box[0]))
    if w <= 0 or h <= 0:
        return None
    dst = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(box.astype(np.float32), dst)
    return cv2.warpPerspective(img, M, (w, h))


# ── 识别后处理 ──────────────────────────────────────────────────────────

def _rec_decode(preds: np.ndarray, char_list: list[str],
                threshold: float = 0.0) -> tuple[list[str], list[float]]:
    """CTC greedy decode。"""
    texts = []
    confs = []
    for pred in preds:
        # pred shape: [seq_len, vocab_size]
        indices = pred.argmax(axis=1)
        probs = pred.max(axis=1)

        chars = []
        scores = []
        prev_idx = -1
        for i, idx in enumerate(indices):
            if idx != prev_idx and idx != 0 and idx < len(char_list) + 1:
                chars.append(char_list[idx - 1])
                scores.append(float(probs[i]))
            prev_idx = idx

        text = "".join(chars)
        if text and scores:
            avg_conf = sum(scores) / len(scores)
            if avg_conf >= threshold:
                texts.append(text)
                confs.append(avg_conf)

    return texts, confs


# ── 主推理流程 ──────────────────────────────────────────────────────────

def _normalize_img(img: np.ndarray, target_h: int, target_w: int) -> np.ndarray:
    """resize + normalize to [-1,1] for PP-OCRv4。"""
    resized = cv2.resize(img, (target_w, target_h))
    # PP-OCRv4: BGR → float32, normalize to [0,1] then to [-1,1]
    img_float = resized.astype(np.float32) / 255.0
    # mean=0.5, std=0.5 → (x - 0.5) / 0.5
    img_norm = (img_float - 0.5) / 0.5
    # HWC → CHW
    return img_norm.transpose(2, 0, 1)


def _run_det(img: np.ndarray) -> list[np.ndarray]:
    """运行文本检测。"""
    h, w = img.shape[:2]

    # 限制短边到 736
    limit_side = 736
    if min(h, w) > limit_side:
        ratio = limit_side / min(h, w)
        new_h, new_w = int(h * ratio), int(w * ratio)
        img_resized = cv2.resize(img, (new_w, new_h))
    else:
        img_resized = img

    # pad 到 32 的倍数
    rh, rw = img_resized.shape[:2]
    pad_h = (32 - rh % 32) % 32
    pad_w = (32 - rw % 32) % 32
    if pad_h > 0 or pad_w > 0:
        img_resized = cv2.copyMakeBorder(img_resized, 0, pad_h, 0, pad_w, cv2.BORDER_CONSTANT, value=0)

    # 预处理: BGR→RGB, normalize
    input_tensor = _normalize_img(img_resized, img_resized.shape[0], img_resized.shape[1])
    input_tensor = input_tensor[np.newaxis, ...]  # add batch dim

    det_input_name = _det_session.get_inputs()[0].name
    outputs = _det_session.run(None, {det_input_name: input_tensor})
    pred_map = outputs[0][0, 0]  # [H, W]

    # 缩放回原图尺寸
    pred_map = cv2.resize(pred_map, (w, h))

    boxes = _det_postprocess(pred_map)

    # 缩放框到原图坐标
    scale_boxes = []
    for box in boxes:
        box[:, 0] = np.clip(box[:, 0], 0, w)
        box[:, 1] = np.clip(box[:, 1], 0, h)
        scale_boxes.append(box)

    return scale_boxes


def _run_rec(crops: list[np.ndarray]) -> tuple[list[str], list[float]]:
    """批量识别裁剪图。"""
    if not crops:
        return [], []

    all_texts = []
    all_confs = []

    # 按宽度排序后 batch 推理
    batch_size = 6
    for i in range(0, len(crops), batch_size):
        batch_crops = crops[i:i + batch_size]
        batch_tensors = []

        for crop in batch_crops:
            # resize 高度到 48，保持宽高比
            h, w = crop.shape[:2]
            ratio = 48.0 / h
            new_w = max(1, int(w * ratio))
            # pad 到宽度 320
            new_w = min(new_w, 320)
            resized = cv2.resize(crop, (new_w, 48))

            # pad right
            if new_w < 320:
                resized = cv2.copyMakeBorder(resized, 0, 0, 0, 320 - new_w,
                                             cv2.BORDER_CONSTANT, value=0)

            tensor = _normalize_img(resized, 48, 320)
            batch_tensors.append(tensor)

        batch_input = np.stack(batch_tensors, axis=0)
        rec_input_name = _rec_session.get_inputs()[0].name
        outputs = _rec_session.run(None, {rec_input_name: batch_input})
        preds = outputs[0]  # [batch, seq_len, vocab_size]

        texts, confs = _rec_decode(preds, _rec_char_list, threshold=0.0)
        all_texts.extend(texts)
        all_confs.extend(confs)

    return all_texts, all_confs


# ── API ─────────────────────────────────────────────────────────────────

class OCRRequest(BaseModel):
    image: str = Field(description="Base64 encoded PNG/JPEG")
    lang: str = Field(default="ch")
    use_angle_cls: bool = Field(default=True)
    threshold: float = Field(default=0.5)


class OCRResponse(BaseModel):
    texts: list[str]
    confidences: list[float]
    combined_text: str
    latency_ms: int


class HealthResponse(BaseModel):
    status: str
    gpu: str
    model: str
    provider: str


@app.get("/health", response_model=HealthResponse)
async def health():
    provider = _det_session.get_providers()[0] if _det_session else "unknown"
    return HealthResponse(status="ok", gpu=_gpu_name, model="PP-OCRv4", provider=provider)


@app.post("/ocr", response_model=OCRResponse)
async def ocr(req: OCRRequest):
    t0 = time.perf_counter()

    try:
        img_bytes = base64.b64decode(req.image)
        img_array = np.frombuffer(img_bytes, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

        if img is None:
            ms = int((time.perf_counter() - t0) * 1000)
            return OCRResponse(texts=[], confidences=[], combined_text="", latency_ms=ms)

        # 1. 检测
        boxes = _run_det(img)

        if not boxes:
            ms = int((time.perf_counter() - t0) * 1000)
            return OCRResponse(texts=[], confidences=[], combined_text="", latency_ms=ms)

        # 2. 裁剪
        crops = []
        for box in boxes:
            crop = _get_crop(img, box)
            if crop is not None:
                crops.append(crop)

        # 3. 识别（跳过角度分类）
        texts, confs = _run_rec(crops)

        # 4. 过滤
        filtered_texts = []
        filtered_confs = []
        for text, conf in zip(texts, confs):
            if conf >= req.threshold and text.strip():
                filtered_texts.append(text.strip())
                filtered_confs.append(conf)

        ms = int((time.perf_counter() - t0) * 1000)
        return OCRResponse(
            texts=filtered_texts,
            confidences=filtered_confs,
            combined_text=" ".join(filtered_texts),
            latency_ms=ms,
        )

    except Exception:
        logger.exception("OCR failed")
        ms = int((time.perf_counter() - t0) * 1000)
        return OCRResponse(texts=[], confidences=[], combined_text="", latency_ms=ms)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8089)

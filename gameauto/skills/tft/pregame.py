"""TFT 预游戏状态机 —— 房间→匹配→接受→进入游戏 (全图 OCR 驱动)。

与 states.py 的无状态每帧 detector 不同, 这段流程是**时序驱动**的:
"点接受后等 15s 还能看到开始游戏则匹配失败" 需要记住点击时刻, 纯每帧
detector 表达不了。所以这里用一个显式状态循环, 每态自带 OCR 节拍/定时。

状态流:
    LOBBY → MATCHING → ACCEPTED_WAIT → IN_GAME
                ↑           │
                └───────────┘  (匹配失败: 又见"开始游戏")

各状态判定 (全图 OCR + 文字匹配, 坐标来自服务端返回的 box):
  - LOBBY:        OCR 含 "开始"(开始游戏) → 点其中心 → MATCHING
  - MATCHING:     每 OCR_INTERVAL 秒一次; 含 "接受" → 点其中心 → ACCEPTED_WAIT
  - ACCEPTED_WAIT: 点接受后窗口期:
                     · 又见 "开始"      → 匹配失败 → 回 LOBBY
                     · stage ROI 出 X-Y → 进入游戏 → IN_GAME
                     · 超 ACCEPT_TIMEOUT 仍无"开始" → 兜底判进 IN_GAME
  - IN_GAME:      打印 + 返回, 交棒给 PLANNING (M3 接)

坐标: OCR box 是截图帧像素 → to_normalized 转 [0-1000], 与 ScrcpyInput 对齐。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from gameauto.utils.coordinate import to_normalized

logger = logging.getLogger("gameauto.tft.pregame")

# ── 可调参数 (真机调参用) ────────────────────────────────────────────────

OCR_URL = "http://127.0.0.1:8089/ocr"   # 别用 localhost (Win→WSL2 IPv6 超时)
OCR_INTERVAL = 3.0                        # MATCHING 时每 N 秒 OCR 一次
ACCEPT_TIMEOUT = 15.0                     # 点接受后等多久判成败
MATCH_TIMEOUT = 45.0                      # 点开始游戏后等多久「接受」, 超时回 LOBBY 重点
OCR_THRESHOLD = 0.5                       # OCR 置信度阈值

# 关键词: 精确匹配 "开始游戏" (不能只匹配 "开始", 会误命中 "战斗开始")
START_KEYWORDS = ("开始游戏", "并始游戏")  # "并" 形近字兜底
ACCEPT_KEYWORDS = ("接受", "接愛")        # "受" 形近字兜底

# stage ROI (与 rois.yaml ocr.stage 一致), 进游戏看这里出 X-Y
STAGE_ROI = (0.3475, 0.0055, 0.4433, 0.0514)
STAGE_PATTERN = re.compile(r"\d+\s*[-\-–—]\s*\d+")  # "1-1" / "2-3" 等

# 状态名
LOBBY = "LOBBY"
MATCHING = "MATCHING"
ACCEPTED_WAIT = "ACCEPTED_WAIT"
IN_GAME = "IN_GAME"


# ── OCR client (全图, 返回带 box 的结构) ────────────────────────────────

@dataclass
class OcrHit:
    text: str
    conf: float
    # box 4 角点像素坐标 [[x,y]*4]
    box: list[list[int]]

    def center_px(self) -> tuple[int, int]:
        xs = [p[0] for p in self.box]
        ys = [p[1] for p in self.box]
        return sum(xs) // len(xs), sum(ys) // len(ys)


@dataclass
class OcrResult:
    hits: list[OcrHit] = field(default_factory=list)
    latency_ms: int = 0

    def find(self, keywords: tuple[str, ...]) -> OcrHit | None:
        """找第一个文本含任一关键词的 hit。"""
        for h in self.hits:
            if any(k in h.text for k in keywords):
                return h
        return None

    @property
    def combined_text(self) -> str:
        return " ".join(h.text for h in self.hits)


def ocr_full(png_bytes: bytes) -> OcrResult:
    """POST 全图到 OCR 服务, 返回带 box 的结果。

    png_bytes: 截图帧的 PNG 编码字节。box 坐标基于该帧像素。
    """
    if not png_bytes:
        return OcrResult()
    try:
        payload = {
            "image": base64.b64encode(png_bytes).decode("ascii"),
            "threshold": OCR_THRESHOLD,
        }
        resp = requests.post(OCR_URL, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.exception("ocr_full 请求失败 (%s)", OCR_URL)
        return OcrResult()

    hits = [
        OcrHit(text=t, conf=c, box=b)
        for t, c, b in zip(
            data.get("texts", []),
            data.get("confidences", []),
            data.get("boxes", []),
        )
        if b  # box 缺失的丢弃
    ]
    return OcrResult(hits=hits, latency_ms=int(data.get("latency_ms", 0)))


def _crop_stage_png(png_bytes: bytes) -> bytes:
    """从全帧 PNG 裁出 stage ROI, 重新编码成 PNG 字节。"""
    import cv2
    import numpy as np

    arr = np.frombuffer(png_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return b""
    h, w = img.shape[:2]
    l, t, r, b = STAGE_ROI
    crop = img[int(t * h):int(b * h), int(l * w):int(r * w)]
    ok, buf = cv2.imencode(".png", crop)
    return buf.tobytes() if ok else b""


def ocr_stage(png_bytes: bytes) -> str:
    """OCR stage ROI, 返回识别文本 (用于匹配 X-Y)。"""
    crop = _crop_stage_png(png_bytes)
    if not crop:
        return ""
    try:
        payload = {
            "image": base64.b64encode(crop).decode("ascii"),
            "threshold": OCR_THRESHOLD,
        }
        resp = requests.post(OCR_URL, json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json().get("combined_text", "")
    except Exception:
        logger.exception("ocr_stage 请求失败")
        return ""


# ── 坐标换算 ────────────────────────────────────────────────────────────

def hit_to_1000(hit: OcrHit, frame_w: int, frame_h: int) -> tuple[int, int]:
    """OCR hit 中心 (帧像素) → 归一化 [0-1000], 供 ScrcpyInput.tap。"""
    cx, cy = hit.center_px()
    return to_normalized(cx, cy, frame_w, frame_h)


# ── 预游戏状态机 ────────────────────────────────────────────────────────

class PreGameDriver:
    """驱动 LOBBY→MATCHING→ACCEPTED_WAIT→IN_GAME 的时序状态机。

    用法:
        drv = PreGameDriver(tap_fn)   # tap_fn(x1000, y1000) -> Awaitable
        await drv.run(grab_png_fn)    # grab_png_fn() -> bytes (当前帧 PNG)
        # run() 在进入 IN_GAME 时返回
    """

    def __init__(self, tap_fn, *, log=logger.info) -> None:
        self._tap = tap_fn          # async (x1000, y1000) -> None
        self._log = log
        self.state = LOBBY
        self._accept_at: float | None = None
        self._last_ocr_at: float = 0.0
        self._match_started_at: float | None = None   # 进 MATCHING 的时刻

    async def run(self, grab_png_fn, *, frame_wh: tuple[int, int]) -> str:
        """主循环。grab_png_fn: () -> bytes(PNG); frame_wh: 截图帧宽高。

        返回最终状态 (目前必然 IN_GAME)。
        """
        w, h = frame_wh
        self._log("[预游戏] 启动, 假设当前在房间主界面")
        while self.state != IN_GAME:
            png = grab_png_fn()
            if self.state == LOBBY:
                await self._step_lobby(png, w, h)
            elif self.state == MATCHING:
                await self._step_matching(png, w, h)
            elif self.state == ACCEPTED_WAIT:
                await self._step_accepted_wait(png, w, h)
            await asyncio.sleep(0.5)
        self._log("[预游戏] 已进入游戏, 交棒 PLANNING")
        return self.state

    # ── LOBBY ──────────────────────────────────────────────────────────

    async def _step_lobby(self, png: bytes, w: int, h: int) -> None:
        res = ocr_full(png)
        hit = res.find(START_KEYWORDS)
        self._log(f"[LOBBY] OCR {res.latency_ms}ms | 全图: {res.combined_text[:60]!r}")
        if hit:
            x, y = hit_to_1000(hit, w, h)
            self._log(f"[LOBBY] 看到「开始游戏」→ 点击 ({x},{y})")
            await self._tap(x, y)
            self.state = MATCHING
            now = time.time()
            self._last_ocr_at = now
            self._match_started_at = now   # 计 MATCH_TIMEOUT 用
            await asyncio.sleep(2.0)  # 给 UI 切换时间

    # ── MATCHING ───────────────────────────────────────────────────────

    async def _step_matching(self, png: bytes, w: int, h: int) -> None:
        now = time.time()
        # 超时: 点开始游戏后 MATCH_TIMEOUT 仍没「接受」→ tap 可能丢了/匹配超时, 回 LOBBY 重点
        if self._match_started_at and now - self._match_started_at > MATCH_TIMEOUT:
            self._log(f"[MATCHING] 超 {MATCH_TIMEOUT:.0f}s 未现「接受」, 回 LOBBY 重试")
            self.state = LOBBY
            self._match_started_at = None
            return
        if now - self._last_ocr_at < OCR_INTERVAL:
            return
        self._last_ocr_at = now
        res = ocr_full(png)
        self._log(f"[MATCHING] OCR {res.latency_ms}ms | 全图: {res.combined_text[:60]!r}")
        hit = res.find(ACCEPT_KEYWORDS)
        if hit:
            x, y = hit_to_1000(hit, w, h)
            self._log(f"[MATCHING] 看到「接受」→ 点击 ({x},{y})")
            await self._tap(x, y)
            self._accept_at = now
            self.state = ACCEPTED_WAIT
            await asyncio.sleep(1.5)

    # ── ACCEPTED_WAIT ──────────────────────────────────────────────────

    async def _step_accepted_wait(self, png: bytes, w: int, h: int) -> None:
        now = time.time()
        # res = ocr_full(png)
        # 1) 又见"开始游戏" → 匹配失败, 回 LOBBY
        # if res.find(START_KEYWORDS):
        #     self._log("[ACCEPTED_WAIT] 又见「开始游戏」→ 匹配失败, 回 LOBBY")
        #     self.state = LOBBY
        #     self._accept_at = None
        #     return
        # 2) stage ROI 出 X-Y → 进入游戏
        stage_txt = ocr_stage(png)
        if stage_txt and STAGE_PATTERN.search(stage_txt):
            self._log(f"[ACCEPTED_WAIT] stage={stage_txt!r} → 进入游戏")
            self.state = IN_GAME
            return
        # 3) 超时兜底: 点接受后超 ACCEPT_TIMEOUT 仍没再看到"开始", 判进游戏
        if self._accept_at and now - self._accept_at > ACCEPT_TIMEOUT:
            self._log(
                f"[ACCEPTED_WAIT] 超 {ACCEPT_TIMEOUT:.0f}s 未再见「开始游戏」, 兜底判进游戏"
            )
            self.state = IN_GAME


# ── 便捷构造: 从 TftActions 风格的 tap 回调 ─────────────────────────────

def make_tap_fn(input_obj) -> Any:
    """包一个 ScrcpyInput 成 async tap_fn(x1000, y1000)。

    input_obj 需有 async tap(x, y, duration_ms)。
    """
    async def _tap(x: int, y: int) -> None:
        await input_obj.tap(x, y, 150)
    return _tap

"""VideoCapture — 用本地视频文件模拟真机推流。

行为对标 scrcpy 的实时帧模型: 后台线程按视频原生 fps 走墙上时钟播放,
持续覆写"最新帧"; 消费方(感知/决策)慢时, 旧帧被直接丢弃, 永远只保留最新。

这使视频回放天然复现真机的实时性 —— bot 感知慢一拍, 视频也不会等它,
下一个 tick 拿到的是若干帧之后的画面。

坐标模型:
    - native_resolution: 视频原始宽高 (坐标映射基准)
    - screenshot()     : 返回最新帧 PNG bytes (BaseCapture 契约)
    - screenshot_bgr() : 返回最新帧 BGR ndarray (感知/预览用, 避免重复编解码)
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from gameauto.core.capture.base import BaseCapture

log = logging.getLogger("gameauto.capture.video")


class VideoCapture(BaseCapture):
    """本地视频作为 BaseCapture, 墙上时钟播放 + 自动丢帧。

    Args:
        video_path: 视频文件路径 (支持中文)。
        speed: 播放倍速。1.0=实时, 2.0=快一倍, 0.5=慢放。
        loop: 视频结束后是否循环。
    """

    def __init__(self, video_path: str | Path, speed: float = 1.0, loop: bool = False) -> None:
        self._path = str(video_path)
        self._speed = max(0.01, speed)
        self._loop = loop

        self._cap: cv2.VideoCapture | None = None
        self._w = 0
        self._h = 0
        self._fps = 0.0
        self._frame_count_total = 0

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        # 最新帧状态 (受 _lock 保护)
        self._lock = threading.Lock()
        self._latest_bgr: np.ndarray | None = None
        self._latest_png: bytes = b""
        self._frame_index = -1          # 当前最新帧在视频中的序号 (0-based)
        self._read_count = 0            # 线程累计写入的帧数
        self._served_read_count = 0     # 上次 screenshot_bgr 取走时的 _read_count
        self._last_advanced = 0         # 上次取帧至今视频推进的帧数 (实时性诊断)

        self._finished = threading.Event()

    # ── BaseCapture 契约 ────────────────────────────────────────────────

    async def connect(self) -> None:
        """打开视频并启动后台播放线程。"""
        # 中文路径: 用 numpy 读首帧探测, 避免 cv2.imread/VideoCapture 中文问题。
        # VideoCapture 对中文路径在多数 OpenCV 版本下可用, 这里加保护性检查。
        cap = cv2.VideoCapture(self._path)
        if not cap.isOpened():
            raise ConnectionError(f"VideoCapture: 无法打开视频 {self._path}")
        self._cap = cap
        self._w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
        self._frame_count_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        log.info("VideoCapture: %s %dx%d @ %.1ffps (%d frames), speed=%.2f",
                 self._path, self._w, self._h, self._fps, self._frame_count_total,
                 self._speed)

        self._stop.clear()
        self._finished.clear()
        self._thread = threading.Thread(target=self._play_loop, daemon=True)
        self._thread.start()

    async def disconnect(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._cap:
            self._cap.release()
            self._cap = None

    async def screenshot(self) -> bytes:
        """最新帧 PNG bytes (BaseCapture 契约)。"""
        with self._lock:
            return self._latest_png

    @property
    def native_resolution(self) -> tuple[int, int]:
        return (self._w, self._h)

    # ── 额外接口 (driver / 预览用) ──────────────────────────────────────

    def screenshot_bgr(self) -> np.ndarray | None:
        """最新帧 BGR ndarray (不编解码, 感知/预览首选)。

        取帧时同步刷新实时性诊断: 自上次取帧以来视频推进了多少帧。
        """
        with self._lock:
            self._last_advanced = max(0, self._read_count - self._served_read_count)
            self._served_read_count = self._read_count
            return self._latest_bgr.copy() if self._latest_bgr is not None else None

    def current_timestamp(self) -> float:
        """当前最新帧在视频中的时间 (秒)。"""
        with self._lock:
            if self._frame_index < 0 or self._fps <= 0:
                return 0.0
            return self._frame_index / self._fps

    def current_frame_index(self) -> int:
        with self._lock:
            return self._frame_index

    def frames_advanced_since_last_serve(self) -> int:
        """上一个 tick 期间视频推进了多少帧 (含被丢的)。

        实时性诊断: 这个数大 = 感知太慢, 视频已经往前走了很远。
        """
        with self._lock:
            return self._last_advanced

    def is_finished(self) -> bool:
        return self._finished.is_set()

    # ── 后台播放线程 ─────────────────────────────────────────────────────

    def _play_loop(self) -> None:
        assert self._cap is not None
        frame_interval = 1.0 / self._fps  # 实时间隔
        play_start = time.perf_counter()
        idx = 0

        while not self._stop.is_set():
            # 对齐墙上时钟: 第 idx 帧应在 (idx * interval / speed) 秒被发布
            target = play_start + (idx * frame_interval) / self._speed
            now = time.perf_counter()
            if target > now:
                time.sleep(min(target - now, frame_interval))

            ok, bgr = self._cap.read()
            if not ok:
                if self._loop:
                    self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    idx = 0
                    play_start = time.perf_counter()
                    continue
                log.info("VideoCapture: 播放结束 (共 %d 帧)", self._read_count)
                self._finished.set()
                return

            # 编码 PNG (落盘/契约用; 感知走 screenshot_bgr 不触发重复编码以外的开销)
            ok_enc, png = cv2.imencode(".png", bgr)
            png_bytes = png.tobytes() if ok_enc else b""

            with self._lock:
                self._latest_bgr = bgr
                self._latest_png = png_bytes
                self._frame_index = idx
                self._read_count += 1

            idx += 1

"""ohscrcpy.Device — 高层、人体工学的设备封装。

把 bridge 的低层函数封装成一个用起来顺手的同步 API:

    from ohscrcpy import Device

    dev = Device(serial="YOUR_SN", sdk_jar="hosScrcpy-1.0.15-beta.jar")
    dev.connect()
    w, h = dev.resolution

    dev.click(w // 2, h // 2)                 # 单击屏幕中心
    dev.multi_click(w // 2, h // 2, times=5)  # 连点 5 次
    dev.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.2), duration_ms=400)
    dev.save_screenshot("shot.png")
    dev.close()

坐标约定: 全部使用**原生像素**坐标 (即 dev.resolution 返回的空间)。
"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path

import numpy as np

from ohscrcpy.bridge import (
    init,
    shutdown,
    screenshot,
    screenshot_bgr,
    resolution,
    output_resolution,
    touch,
    swipe,
)

log = logging.getLogger("ohscrcpy")


class Device:
    """鸿蒙设备的低延迟截屏 + 触控封装。

    Args:
        serial:    设备序列号 (``hdc list targets`` 查看)。
        sdk_jar:   HOScrcpy SDK JAR 路径。
        java_home: JDK/JRE 路径，留空自动探测。
        scale:     截图输出缩放系数，1=原分辨率，2=二分之一，3=三分之一 ...
        max_fps:   视频流帧率上限 (1-60)。
        jitter:    点击坐标随机抖动像素数 (人手模拟)，0 关闭。
    """

    def __init__(
        self,
        serial: str,
        sdk_jar: str,
        java_home: str = "",
        scale: int = 2,
        max_fps: int = 30,
        jitter: int = 3,
    ) -> None:
        self._serial = serial
        self._sdk_jar = sdk_jar
        self._java_home = java_home
        self._scale = max(1, scale)
        self._max_fps = max_fps
        self._jitter = max(0, jitter)
        self._connected = False

    # ── 连接管理 ──────────────────────────────────────────────────

    def connect(self) -> None:
        """启动 JVM、连接设备、开启视频流。幂等。"""
        if self._connected:
            return
        init(
            self._serial,
            self._sdk_jar,
            self._java_home,
            scale=self._scale,
            max_fps=self._max_fps,
        )
        w, h = self.resolution
        if not w or not h:
            raise ConnectionError("ohscrcpy: stream failed, resolution is 0x0")
        self._connected = True
        log.info("ohscrcpy connected: native %dx%d, scale=%d, %dfps",
                 w, h, self._scale, self._max_fps)

    def close(self) -> None:
        """停止视频流并关闭 JVM。"""
        self._connected = False
        shutdown()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def resolution(self) -> tuple[int, int]:
        """设备原生分辨率 (点击/滑动坐标使用的空间)。"""
        return resolution()

    @property
    def output_resolution(self) -> tuple[int, int]:
        """缩放后的输出分辨率 (截图返回的帧尺寸)。"""
        return output_resolution()

    # ── 截图 ─────────────────────────────────────────────────────

    def screenshot(self, wait_new: bool = False) -> bytes:
        """返回最新帧 PNG bytes (输出分辨率)。效率优先: 默认直接读缓存(~0.5ms)。

        wait_new=True 等一帧新画面(点击后避免过期帧, scale=2 下默认即可)。
        """
        return screenshot(wait_new=wait_new)

    def screenshot_bgr(self) -> np.ndarray:
        """返回最新帧 BGR numpy 数组 (输出分辨率)，便于 OpenCV 处理。"""
        return screenshot_bgr()

    def save_screenshot(self, path: str | Path, wait_new: bool = True) -> Path:
        """截图并保存为 PNG。返回写入的路径。"""
        png = self.screenshot(wait_new=wait_new)
        p = Path(path)
        p.write_bytes(png)
        return p

    # ── 触控 ─────────────────────────────────────────────────────

    def click(self, x: int, y: int, duration_ms: int = 50) -> float:
        """单次点击 (原生像素坐标)。duration_ms>~500 时等同于长按。返回耗时(ms)。"""
        x, y = self._apply_jitter(x, y)
        return touch(x, y, duration_ms)

    def tap(self, x: int, y: int, duration_ms: int = 50) -> float:
        """点击 (click 别名, 原生像素坐标)。"""
        return self.click(x, y, duration_ms)

    def long_press(self, x: int, y: int, duration_ms: int = 1000) -> float:
        """长按 (原生像素坐标)。"""
        x, y = self._apply_jitter(x, y)
        return touch(x, y, duration_ms)

    def multi_click(
        self,
        x: int,
        y: int,
        times: int = 5,
        interval: float = 0.1,
        duration_ms: int = 50,
    ) -> None:
        """连点 —— 在同一位置快速点击 ``times`` 次。

        Args:
            x, y:        原生像素坐标。
            times:       点击次数。
            interval:    两次点击之间的间隔 (秒)。
            duration_ms: 单次按下时长 (ms)。
        """
        for i in range(max(1, times)):
            self.click(x, y, duration_ms=duration_ms)
            if i < times - 1 and interval > 0:
                time.sleep(interval)

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 500,
    ) -> float:
        """滑动 (原生像素坐标)。返回耗时(ms)。"""
        x1, y1 = self._apply_jitter(x1, y1)
        x2, y2 = self._apply_jitter(x2, y2)
        return swipe(x1, y1, x2, y2, duration_ms)

    # ── 内部 ─────────────────────────────────────────────────────

    def _apply_jitter(self, x: int, y: int) -> tuple[int, int]:
        if self._jitter <= 0:
            return int(x), int(y)
        return (
            int(x) + random.randint(-self._jitter, self._jitter),
            int(y) + random.randint(-self._jitter, self._jitter),
        )

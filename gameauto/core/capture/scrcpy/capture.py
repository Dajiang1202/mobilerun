"""ScrcpyCapture — ~17ms screenshot via gRPC H.264 video stream."""

from __future__ import annotations

import asyncio
import logging

from gameauto.core.capture.base import BaseCapture
from gameauto.core.capture.scrcpy.bridge import (
    init, shutdown, screenshot, resolution,
)

log = logging.getLogger("gameauto.capture.scrcpy")


class ScrcpyCapture(BaseCapture):
    """Low-latency screen capture via HOScrcpy SDK (JPype bridge).

    ``connect()`` starts JVM + video stream.  ``screenshot()`` returns
    the latest decoded frame as PNG bytes (~17ms vs ~500ms hdc).

    Resolution: native is auto-detected, output = native / scale.
    Touch coordinates are always in native pixel space.

    Usage::

        capture = ScrcpyCapture("serial")          # scale=2 (half), 30fps
        capture = ScrcpyCapture("serial", scale=3) # third resolution
        capture = ScrcpyCapture("serial", bitrate=1_000_000)  # 1Mbps低功耗
        await capture.connect()
        png = await capture.screenshot()           # output resolution
        w, h = capture.native_resolution            # native (for coords)
    """

    _DEFAULT_SDK_JAR = "D:/resource/hosScrcpy-1.0.15-beta.jar"
    _DEFAULT_SCALE = 2
    _DEFAULT_FPS = 30

    def __init__(self, serial: str, sdk_jar: str = "", java_home: str = "",
                 scale: int = 0, max_fps: int = 0,
                 bitrate: int = 0, i_frame_interval: int = 0) -> None:
        self._serial = serial
        self._sdk_jar = sdk_jar or self._DEFAULT_SDK_JAR
        self._java_home = java_home
        self._scale = scale or self._DEFAULT_SCALE
        self._fps = max_fps or self._DEFAULT_FPS
        self._bitrate = bitrate
        self._i_frame_interval = i_frame_interval
        self._connected = False
        self._w = 0
        self._h = 0

    async def connect(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._connect_sync)
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def screenshot(self) -> bytes:
        if not self._connected:
            await self.connect()
        loop = asyncio.get_event_loop()
        # wait_new=True: 等新帧到达再返回, 避免读到点击前的过期缓存帧(重复操作根因)
        return await loop.run_in_executor(None, lambda: screenshot(wait_new=True))

    @property
    def native_resolution(self) -> tuple[int, int]:
        # Dynamic: bridge globals update on screen rotation (portrait↔landscape)
        w, h = resolution()
        if w and h:
            self._w, self._h = w, h  # keep cached in sync
        return (self._w, self._h)

    def _connect_sync(self) -> None:
        init(self._serial, self._sdk_jar, self._java_home,
             scale=self._scale, max_fps=self._fps,
             bitrate=self._bitrate, i_frame_interval=self._i_frame_interval)
        self._w, self._h = resolution()
        if not self._w or not self._h:
            raise ConnectionError("ScrcpyCapture: stream failed, resolution is 0x0")
        log.info("ScrcpyCapture: native %dx%d, scale=%d, %dfps, bitrate=%s",
                 self._w, self._h, self._scale, self._fps,
                 f"{self._bitrate/1_000_000:.1f}Mbps" if self._bitrate else "default")

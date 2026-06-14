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
        await capture.connect()
        png = await capture.screenshot()           # output resolution
        w, h = capture.native_resolution            # native (for coords)
    """

    _DEFAULT_SDK_JAR = "gameauto/resource/hosScrcpy-1.0.15-beta.jar"
    _DEFAULT_SCALE = 2
    _DEFAULT_FPS = 30

    def __init__(self, serial: str, sdk_jar: str = "", java_home: str = "",
                 scale: int = 0, max_fps: int = 0) -> None:
        self._serial = serial
        self._sdk_jar = sdk_jar or self._DEFAULT_SDK_JAR
        self._java_home = java_home
        self._scale = scale or self._DEFAULT_SCALE
        self._fps = max_fps or self._DEFAULT_FPS
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
        return await loop.run_in_executor(None, screenshot)

    @property
    def native_resolution(self) -> tuple[int, int]:
        return (self._w, self._h)

    def _connect_sync(self) -> None:
        init(self._serial, self._sdk_jar, self._java_home,
             scale=self._scale, max_fps=self._fps)
        self._w, self._h = resolution()
        log.info("ScrcpyCapture: native %dx%d, scale=%d, %dfps",
                 self._w, self._h, self._scale, self._fps)

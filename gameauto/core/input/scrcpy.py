"""ScrcpyInput — ~0.01ms touch/swipe via HOScrcpy SDK.

Shares the JVM started by ScrcpyCapture (bridge.init is idempotent).
"""

from __future__ import annotations

import asyncio
import logging
import random

from gameauto.core.input.base import BaseInput
from gameauto.core.capture.scrcpy.bridge import (
    init, touch, swipe, resolution,
)
from gameauto.utils.coordinate import to_absolute

log = logging.getLogger("gameauto.input.scrcpy")


class ScrcpyInput(BaseInput):
    """Touch/swipe via HOScrcpy SDK (JPype bridge).

    Coordinates: normalized [0-1000] → absolute pixels.

    Usage::

        inp = ScrcpyInput("serial", "sdk.jar", resolution="720p")
        await inp.connect()
        inp.set_input_resolution(405, 720)
        await inp.tap(500, 500)
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
        self._iw: int | None = None
        self._ih: int | None = None

    async def connect(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._connect_sync)
        self._connected = True

    async def tap(self, x: float, y: float, duration_ms: int = 100) -> None:
        w, h = self._check_resolution()
        px, py = to_absolute(x, y, w, h)
        px += random.randint(-3, 3)
        py += random.randint(-3, 3)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: touch(px, py, duration_ms))

    async def swipe(self, x1: float, y1: float, x2: float, y2: float,
                    duration_ms: int = 1000) -> None:
        w, h = self._check_resolution()
        px1, py1 = to_absolute(x1, y1, w, h)
        px2, py2 = to_absolute(x2, y2, w, h)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: swipe(px1, py1, px2, py2, duration_ms))

    @property
    def input_resolution(self) -> tuple[int, int]:
        return (self._iw or 0, self._ih or 0)

    def set_input_resolution(self, w: int, h: int) -> None:
        self._iw = w
        self._ih = h

    def _connect_sync(self) -> None:
        init(self._serial, self._sdk_jar, self._java_home,
             scale=self._scale, max_fps=self._fps)
        if not self._iw:
            self._iw, self._ih = resolution()

    def _check_resolution(self) -> tuple[int, int]:
        w, h = self.input_resolution
        if not w or not h:
            raise RuntimeError("set_input_resolution() first")
        return w, h

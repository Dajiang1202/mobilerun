"""HarmonyOS input simulation via HDC."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import shutil

from gameauto.core.input.base import BaseInput
from gameauto.utils.coordinate import to_absolute

logger = logging.getLogger("gameauto.input.hdc")


class HdcInput(BaseInput):
    """Input simulation for HarmonyOS devices via HDC subprocess.

    All coordinates are normalized [0-1000], automatically converted
    to absolute pixels based on input_resolution.

    Args:
        serial: Device identifier for ``hdc -t <serial>``.
        hdc_path: Path to hdc binary (default "hdc").
        input_width, input_height: Input coordinate space (defaults to
            capture resolution if not specified).
    """

    def __init__(
        self,
        serial: str | None = None,
        hdc_path: str = "hdc",
        input_width: int | None = None,
        input_height: int | None = None,
    ) -> None:
        self._serial: str | None = serial
        self._hdc_path: str = hdc_path
        self._connected: bool = False
        self._resolved_hdc: str | None = None
        self._input_w: int | None = input_width
        self._input_h: int | None = input_height

    # ── Connection ──────────────────────────────────────────────────

    async def connect(self) -> None:
        resolved = shutil.which(self._hdc_path)
        if not resolved:
            raise ConnectionError(f"HDC binary not found: {self._hdc_path!r}")
        self._resolved_hdc = resolved

        returncode, _, stderr = await self._hdc("list", "targets")
        if returncode != 0:
            raise ConnectionError(f"HDC list targets failed: {stderr.decode(errors='replace')}")
        self._connected = True
        logger.info("HDC input connected for device%s", f" ({self._serial})" if self._serial else "")

    @property
    def input_resolution(self) -> tuple[int, int]:
        if self._input_w and self._input_h:
            return self._input_w, self._input_h
        return 0, 0

    def set_input_resolution(self, w: int, h: int) -> None:
        """Set the input coordinate space (usually from capture resolution)."""
        self._input_w = w
        self._input_h = h

    # ── Actions ──────────────────────────────────────────────────────

    async def tap(self, x: float, y: float, duration_ms: int = 100) -> None:
        w, h = self._check_resolution()
        px, py = to_absolute(x, y, w, h)
        # Add ±3px human-like jitter
        px += random.randint(-3, 3)
        py += random.randint(-3, 3)

        rc, _, stderr = await self._hdc("shell", "uitest", "uiInput", "click", str(px), str(py))
        if rc != 0:
            raise RuntimeError(f"tap failed: {stderr.decode(errors='replace')}")

    async def swipe(
        self, x1: float, y1: float, x2: float, y2: float, duration_ms: int = 1000,
    ) -> None:
        w, h = self._check_resolution()
        px1, py1 = to_absolute(x1, y1, w, h)
        px2, py2 = to_absolute(x2, y2, w, h)

        distance = math.hypot(px2 - px1, py2 - py1)
        velocity = max(1, int(distance / max(duration_ms / 1000.0, 0.001)))

        rc, _, stderr = await self._hdc(
            "shell", "uitest", "uiInput", "swipe",
            str(px1), str(py1), str(px2), str(py2), str(velocity),
        )
        if rc != 0:
            raise RuntimeError(f"swipe failed: {stderr.decode(errors='replace')}")

        await asyncio.sleep(duration_ms / 1000.0)

    # ── Internal ─────────────────────────────────────────────────────

    def _check_resolution(self) -> tuple[int, int]:
        w, h = self.input_resolution
        if not w or not h:
            raise RuntimeError("Input resolution not set. Call set_input_resolution() first.")
        return w, h

    async def _hdc(self, *args: str, timeout: float = 30.0) -> tuple[int, bytes, bytes]:
        assert self._resolved_hdc, "connect() must be called first"
        cmd = [self._resolved_hdc]
        if self._serial:
            cmd.extend(("-t", self._serial))
        cmd.extend(args)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise ConnectionError(f"HDC command timed out ({timeout}s): {' '.join(cmd)}") from exc
        return proc.returncode or 0, stdout, stderr

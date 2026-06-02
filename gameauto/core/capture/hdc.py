"""HarmonyOS screen capture via HDC (HarmonyOS Device Connector)."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
from pathlib import Path

from gameauto.core.capture.base import BaseCapture

logger = logging.getLogger("gameauto.capture.hdc")


class HdcCapture(BaseCapture):
    """Screen capture for HarmonyOS devices via HDC subprocess.

    Supports two backends:
    - ``snapshot``: hdc shell snapshot_display → JPEG (fast, single command)
    - ``screenCap``: uitest capture + file recv → PNG (reliable, two commands)

    Args:
        serial: Device identifier for ``hdc -t <serial>``.
        hdc_path: Path to hdc binary (default "hdc").
        screenshot_method: "auto", "snapshot", or "screenCap".
    """

    def __init__(
        self,
        serial: str | None = None,
        hdc_path: str = "hdc",
        screenshot_method: str = "auto",
    ) -> None:
        self._serial: str | None = serial
        self._hdc_path: str = hdc_path
        self._screenshot_method: str = screenshot_method
        self._connected: bool = False
        self._resolved_hdc: str | None = None
        self._native_w: int = 0
        self._native_h: int = 0

    # ── Connection ──────────────────────────────────────────────────

    async def connect(self) -> None:
        resolved = shutil.which(self._hdc_path)
        if not resolved:
            raise ConnectionError(
                f"HDC binary not found: {self._hdc_path!r}. Install HarmonyOS SDK or add hdc to PATH."
            )
        self._resolved_hdc = resolved

        returncode, stdout, stderr = await self._hdc("list", "targets")
        if returncode != 0:
            err = stderr.decode(errors="replace").strip()
            if "5037" in err or "address already in use" in err.lower():
                raise ConnectionError(
                    f"HDC failed (port conflict): {err}\n"
                    "ADB and HDC both default to port 5037. Try: adb kill-server"
                )
            raise ConnectionError(f"HDC list targets failed: {err}")

        output = stdout.decode(errors="replace").strip()
        if not output:
            raise ConnectionError("No HarmonyOS device found via HDC.")

        self._connected = True

        # Get resolution from first screenshot
        first = await self.screenshot()
        from gameauto.utils.images import image_dimensions
        self._native_w, self._native_h = image_dimensions(first)
        logger.info(
            "Connected to HarmonyOS device%s, resolution %dx%d",
            f" ({self._serial})" if self._serial else "",
            self._native_w, self._native_h,
        )

    async def disconnect(self) -> None:
        self._connected = False
        self._resolved_hdc = None

    @property
    def native_resolution(self) -> tuple[int, int]:
        return self._native_w, self._native_h

    # ── Screenshot ────────────────────────────────────────────────────

    async def screenshot(self) -> bytes:
        if not self._connected:
            await self.connect()

        if self._screenshot_method in ("snapshot", "auto"):
            try:
                return await self._screenshot_via_snapshot_display()
            except Exception as e:
                if self._screenshot_method == "snapshot":
                    raise
                logger.debug("snapshot_display failed, falling back to screenCap: %s", e)

        return await self._screenshot_via_screencap()

    async def _screenshot_via_snapshot_display(self) -> bytes:
        rc, stdout, stderr = await self._hdc("shell", "snapshot_display")
        if rc != 0:
            raise RuntimeError(f"snapshot_display failed: {stderr.decode(errors='replace')}")

        output = stdout.decode(errors="replace")
        match = re.search(r"write to ([^\s]+)", output)
        if not match:
            raise RuntimeError(f"Could not parse snapshot path from: {output}")
        remote_path = match.group(1)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
            local_path = Path(f.name)
        try:
            rc, _, stderr = await self._hdc("file", "recv", remote_path, str(local_path))
            if rc != 0:
                raise RuntimeError(f"Failed to receive screenshot: {stderr.decode(errors='replace')}")
            return local_path.read_bytes()
        finally:
            local_path.unlink(missing_ok=True)
            try:
                await self._hdc("shell", "rm", "-f", remote_path)
            except Exception:
                pass

    async def _screenshot_via_screencap(self) -> bytes:
        remote_path = "/data/local/tmp/gameauto_screenshot.png"

        rc, _, stderr = await self._hdc("shell", "uitest", "screenCap", remote_path)
        if rc != 0:
            # Try with -p flag
            rc, _, stderr = await self._hdc("shell", "uitest", "screenCap", "-p", remote_path)
            if rc != 0:
                # Try without path (stdout)
                rc, stdout, stderr = await self._hdc("shell", "uitest", "screenCap")
                if rc == 0 and stdout:
                    return stdout
                raise RuntimeError(f"screenCap failed: {stderr.decode(errors='replace')}")

        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
            local_path = Path(f.name)
        try:
            rc, _, stderr = await self._hdc("file", "recv", remote_path, str(local_path))
            if rc != 0:
                raise RuntimeError(f"Failed to receive screenshot: {stderr.decode(errors='replace')}")
            return local_path.read_bytes()
        finally:
            local_path.unlink(missing_ok=True)
            try:
                await self._hdc("shell", "rm", "-f", remote_path)
            except Exception:
                pass

    # ── Internal ───────────────────────────────────────────────────────

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

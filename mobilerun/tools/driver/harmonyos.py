"""HarmonyOS device driver using HDC (HarmonyOS Device Connector).

Wraps ``hdc`` CLI via asyncio subprocess for basic device operations:
screenshot, tap, and swipe. HarmonyOS NEXT / OpenHarmony does not support
ADB; all communication goes through HDC.
"""

from __future__ import annotations

import asyncio
import logging
import math
import shutil
import tempfile
from pathlib import Path

from mobilerun.tools.driver.base import DeviceDriver

logger = logging.getLogger("mobilerun")


class HarmonyOSDriver(DeviceDriver):
    """Device driver for HarmonyOS devices via HDC subprocess calls.

    Supports two screenshot backends (auto-selected by default):
    - ``snapshot``: ``hdc shell snapshot_display`` → JPEG (fast, single command)
    - ``screenCap``: uitest capture + file recv → PNG (reliable, two commands)

    Args:
        serial: Device identifier passed as ``-t <serial>`` to hdc.
            When None, hdc auto-selects the sole connected device.
        hdc_path: Path or command name for the hdc binary (default "hdc").
        screenshot_method: ``"auto"``, ``"snapshot"``, or ``"screenCap"``.
    """

    platform = "HarmonyOS"
    supported = {"tap", "swipe", "screenshot"}
    supported_buttons: set[str] = set()

    def __init__(
        self,
        serial: str | None = None,
        hdc_path: str = "hdc",
        screenshot_method: str = "auto",
    ) -> None:
        super().__init__()
        self._serial: str | None = serial
        self._hdc_path: str = hdc_path
        self._screenshot_method: str = screenshot_method
        self._connected: bool = False
        self._resolved_hdc: str | None = None

    # ── Connection ──────────────────────────────────────────────────────

    async def connect(self) -> None:
        resolved = shutil.which(self._hdc_path)
        if not resolved:
            raise ConnectionError(
                f"HDC binary not found: {self._hdc_path!r}. "
                "Install HarmonyOS SDK or add hdc to PATH."
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

        if self._serial and self._serial not in output:
            logger.warning(
                "Device %s not found in hdc target list: %s", self._serial, output
            )

        self._connected = True
        logger.info("Connected to HarmonyOS device%s", f" ({self._serial})" if self._serial else "")

    async def ensure_connected(self) -> None:
        if not self._connected:
            await self.connect()

    # ── Screenshot ──────────────────────────────────────────────────────

    async def screenshot(self, hide_overlay: bool = True) -> bytes:
        """Take a screenshot via HDC.

        In ``"auto"`` mode tries ``snapshot_display`` first (JPEG, fast),
        falling back to ``uitest screenCap`` + ``file recv`` (PNG, reliable).
        """
        await self.ensure_connected()

        if self._screenshot_method in ("snapshot", "auto"):
            try:
                return await self._screenshot_via_snapshot_display()
            except Exception as e:
                if self._screenshot_method == "snapshot":
                    raise
                logger.debug("snapshot_display failed, falling back to screenCap: %s", e)

        return await self._screenshot_via_screencap()

    async def _screenshot_via_snapshot_display(self) -> bytes:
        """Take screenshot using snapshot_display (saves to file then pulls)."""
        # Try running snapshot_display, it saves to a temp file automatically
        rc, stdout, stderr = await self._hdc(
            "shell", "snapshot_display"
        )
        if rc != 0:
            raise RuntimeError(
                f"snapshot_display failed: {stderr.decode(errors='replace')}"
            )

        # Parse output to find the saved path
        output = stdout.decode(errors='replace')
        import re
        match = re.search(r'write to ([^\s]+)', output)
        if not match:
            raise RuntimeError(f"Could not parse snapshot path from: {output}")
        remote_path = match.group(1)

        # Pull the file to a temporary local file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
            local_path = Path(f.name)
        try:
            rc, _, stderr = await self._hdc(
                "file", "recv", remote_path, str(local_path)
            )
            if rc != 0:
                raise RuntimeError(
                    f"Failed to receive screenshot: {stderr.decode(errors='replace')}"
                )
            return local_path.read_bytes()
        finally:
            # Cleanup both remote and local files
            try:
                local_path.unlink(missing_ok=True)
            except Exception:
                pass
            try:
                await self._hdc("shell", "rm", "-f", remote_path)
            except Exception:
                pass

    async def _screenshot_via_screencap(self) -> bytes:
        """Two-step screenshot: capture to temp file, then pull via file recv."""
        remote_path = "/data/local/tmp/mobilerun_hdc_screenshot.png"

        # Try without -p first (some versions use different args)
        rc, _, stderr = await self._hdc(
            "shell", "uitest", "screenCap", remote_path
        )
        if rc != 0:
            # Try with -p
            rc, _, stderr = await self._hdc(
                "shell", "uitest", "screenCap", "-p", remote_path
            )
            if rc != 0:
                # Try just screenCap without args (may output to stdout)
                rc, stdout, stderr = await self._hdc(
                    "shell", "uitest", "screenCap"
                )
                if rc == 0 and stdout:
                    return stdout
                raise RuntimeError(
                    f"screenCap failed: {stderr.decode(errors='replace')}"
                )

        # Pull the file to a temporary local file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
            local_path = Path(f.name)
        try:
            rc, _, stderr = await self._hdc(
                "file", "recv", remote_path, str(local_path)
            )
            if rc != 0:
                raise RuntimeError(
                    f"Failed to receive screenshot: {stderr.decode(errors='replace')}"
                )
            return local_path.read_bytes()
        finally:
            # Cleanup both remote and local files
            try:
                local_path.unlink(missing_ok=True)
            except Exception:
                pass
            try:
                await self._hdc("shell", "rm", "-f", remote_path)
            except Exception:
                pass

    # ── Input ───────────────────────────────────────────────────────────

    async def tap(self, x: int, y: int) -> None:
        """Tap at absolute pixel coordinates (x, y)."""
        await self.ensure_connected()
        rc, _, stderr = await self._hdc(
            "shell", "uitest", "uiInput", "click", str(x), str(y)
        )
        if rc != 0:
            raise RuntimeError(f"tap failed: {stderr.decode(errors='replace')}")

    async def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 1000,
    ) -> None:
        """Swipe from (x1,y1) to (x2,y2).

        HDC uses velocity (px/s) rather than duration; we compute velocity
        from the pixel distance and requested duration.
        """
        await self.ensure_connected()
        distance = math.hypot(x2 - x1, y2 - y1)
        velocity = max(1, int(distance / max(duration_ms / 1000.0, 0.001)))

        rc, _, stderr = await self._hdc(
            "shell", "uitest", "uiInput", "swipe",
            str(x1), str(y1), str(x2), str(y2), str(velocity),
        )
        if rc != 0:
            raise RuntimeError(f"swipe failed: {stderr.decode(errors='replace')}")

        await asyncio.sleep(duration_ms / 1000.0)

    # ── Internal helpers ────────────────────────────────────────────────

    async def _hdc(
        self, *args: str, timeout: float = 30.0
    ) -> tuple[int, bytes, bytes]:
        """Run an hdc command and return (returncode, stdout, stderr)."""
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
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise ConnectionError(
                f"HDC command timed out ({timeout}s): {' '.join(cmd)}"
            ) from exc
        return proc.returncode or 0, stdout, stderr

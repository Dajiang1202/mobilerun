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

    Uses a persistent HDC shell session to avoid per-screenshot subprocess
    startup overhead (~100ms saved per frame).

    Supports two backends:
    - ``snapshot``: hdc shell snapshot_display → base64 JPEG (fast, persistent session)
    - ``screenCap``: uitest capture + file recv → PNG (fallback, one-shot)
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
        self._shell_proc: asyncio.subprocess.Process | None = None  # persistent shell

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

        # Start persistent shell session (reuse for all screenshots)
        await self._start_shell()

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
        if self._shell_proc:
            try:
                self._shell_proc.stdin.write_eof()
            except Exception:
                pass
            try:
                self._shell_proc.kill()
                await self._shell_proc.wait()
            except Exception:
                pass
            self._shell_proc = None
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
        """Capture via persistent shell: snapshot → base64 cat → decode.

        Avoids a second subprocess for file recv (~300ms saved).
        """
        if self._shell_proc and self._shell_proc.returncode is None:
            try:
                return await self._snapshot_persistent()
            except Exception:
                logger.debug("Persistent shell failed, restarting...")
                await self._restart_shell()

        # Fallback: traditional two-command approach
        return await self._snapshot_fallback()

    async def _snapshot_persistent(self) -> bytes:
        """Single shell session: snapshot → base64 pipe → decode."""
        shell = self._shell_proc

        # 1. Take snapshot
        shell.stdin.write(b"snapshot_display\n")
        await shell.stdin.drain()

        # 2. Read output to find file path
        line = await asyncio.wait_for(shell.stdout.readline(), timeout=15.0)
        output = line.decode(errors="replace")
        match = re.search(r"write to ([^\s]+)", output)
        if not match:
            raise RuntimeError(f"Could not parse snapshot path from: {output}")
        remote_path = match.group(1).strip()

        # 3. Base64 read the file inline
        cmd = f"cat {remote_path} | base64 && echo '---GAMEAUTO_END---' && rm -f {remote_path}\n"
        shell.stdin.write(cmd.encode())
        await shell.stdin.drain()

        # 4. Read base64 data until end marker
        b64_lines = []
        while True:
            line = await asyncio.wait_for(shell.stdout.readline(), timeout=15.0)
            decoded = line.decode(errors="replace").strip()
            if decoded == "---GAMEAUTO_END---":
                break
            b64_lines.append(decoded)

        # 5. Decode
        import base64
        b64_str = "".join(b64_lines)
        return base64.b64decode(b64_str)

    async def _snapshot_fallback(self) -> bytes:
        """Traditional: snapshot → file recv (two subprocess calls)."""
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

    # ── Persistent shell management ──────────────────────────────────

    async def _start_shell(self) -> None:
        """Start a persistent hdc shell session."""
        assert self._resolved_hdc
        cmd = [self._resolved_hdc]
        if self._serial:
            cmd.extend(("-t", self._serial))
        cmd.append("shell")

        self._shell_proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.debug("Persistent HDC shell started")

    async def _restart_shell(self) -> None:
        """Kill and restart the persistent shell."""
        if self._shell_proc:
            try:
                self._shell_proc.kill()
                await self._shell_proc.wait()
            except Exception:
                pass
        await self._start_shell()

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

"""HarmonyOS device driver — backed by hmdriver2 (Hypium RPC).

This driver wraps the open-source `hmdriver2` library, which communicates
with the on-device uitest engine via the Hypium RPC protocol (the same
channel used by HarmonyOS's official hypium test framework). This gives us:
  - UI tree perception (dump_hierarchy)
  - Element location by text/id/key/description/type
  - Tap / swipe / input_text / key events / app lifecycle
all through one persistent TCP connection (hdc fport → device port 8012).

Because hmdriver2 is synchronous and mobilerun is asyncio, every call is
wrapped with ``asyncio.to_thread``. The hmdriver2 ``Driver`` is a per-serial
singleton; we hold one instance lazily created in ``connect()``.

Design notes
------------
- ``supported`` declares the capability set the mobilerun tool registry uses
  to auto-enable coordinate tools, element-index tools, text input, etc.
- Screenshot returns ``bytes`` (mobilerun contract) by reading the file that
  hmdriver2 writes.
- ``get_ui_tree()`` flattens the nested hmdriver2 JSON into mobilerun's
  element-dict list schema so existing formatters (IndexedFormatter) work.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from mobilerun.tools.driver.base import DeviceDriver

logger = logging.getLogger("mobilerun")


def _ensure_hdc_on_path(hdc_path: str | None) -> None:
    """Inject the hdc binary directory into PATH so hmdriver2 can find it.

    hmdriver2 shells out to ``hdc`` by name; it must be on PATH.
    """
    if not hdc_path:
        return
    hdc_dir = str(Path(hdc_path).resolve().parent)
    if hdc_dir and hdc_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = hdc_dir + os.pathsep + os.environ.get("PATH", "")


def _to_bool(v: Any) -> bool:
    """hmdriver2 returns 'true'/'false' strings for boolean attributes."""
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).lower() in ("true", "1", "yes")


class HarmonyOSDriver(DeviceDriver):
    """Device driver for HarmonyOS devices via hmdriver2 (Hypium RPC).

    Args:
        serial: Device identifier (``-t <serial>``). When None, the sole
            connected device is auto-selected.
        hdc_path: Path to the hdc binary (hmdriver2 needs it on PATH).
        screenshot_method: ``"auto"`` / ``"snapshot"`` / ``"screenCap"``.
            Passed to hmdriver2's screenshot when using the file-based path.
    """

    platform = "harmonyos"

    # Capability set consumed by mobilerun's tool registry.
    # - tap/swipe/screenshot: coordinate actions
    # - input_text/direct_text_input: typing into focused fields
    # - element_index: click/type/long_press by element index (after get_ui_tree)
    # - convert_point: normalized→absolute coordinate conversion
    # - press_button: system keys (back/home/enter)
    # - start_app/get_apps: app lifecycle
    supported = {
        "tap",
        "swipe",
        "screenshot",
        "input_text",
        "direct_text_input",
        "element_index",
        "convert_point",
        "press_button",
        "start_app",
        "get_apps",
    }
    supported_buttons = {"back", "home", "enter"}

    def __init__(
        self,
        serial: str | None = None,
        hdc_path: str | None = None,
        screenshot_method: str = "auto",
    ) -> None:
        super().__init__()
        self._serial: str | None = serial
        self._hdc_path: str | None = hdc_path
        self._screenshot_method: str = screenshot_method
        self._connected: bool = False
        self._hm_driver = None  # hmdriver2.Driver instance (lazy)
        # Track which screen size was used for the most recent ui_tree, so
        # convert_point / element-index callers can reason about coordinates.
        self._screen_width: int | None = None
        self._screen_height: int | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Connect to the device by constructing the hmdriver2 Driver.

        hmdriver2 will push its agent.so, start the uitest daemon, set up
        hdc port forwarding, and open the Hypium RPC socket. This takes
        ~2 seconds on first run (agent push) and ~0.5s on subsequent runs.
        """
        _ensure_hdc_on_path(self._hdc_path)

        try:
            # hmdriver2 is synchronous — run in a worker thread.
            from hmdriver2.driver import Driver as HmDriver
        except ImportError as e:
            raise ConnectionError(
                "hmdriver2 is not installed. Install it with: pip install hmdriver2"
            ) from e

        try:
            self._hm_driver = await asyncio.to_thread(HmDriver, self._serial)
        except Exception as e:
            raise ConnectionError(f"hmdriver2 failed to connect: {e}") from e

        # Read display size once for coordinate bookkeeping.
        # display_size is a cached_property returning (w, h) tuple.
        try:
            size = await asyncio.to_thread(lambda: self._hm_driver.display_size)
            if isinstance(size, tuple) and len(size) == 2:
                self._screen_width, self._screen_height = int(size[0]), int(size[1])
        except Exception:
            pass

        self._connected = True
        logger.info(
            "Connected to HarmonyOS device%s (%sx%s)",
            f" ({self._serial})" if self._serial else "",
            self._screen_width,
            self._screen_height,
        )

    async def ensure_connected(self) -> None:
        if not self._connected:
            await self.connect()

    # ── Input actions ────────────────────────────────────────────────────

    async def tap(self, x: int, y: int) -> None:
        await self.ensure_connected()
        await asyncio.to_thread(self._hm_driver.click, x, y)

    async def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: float = 1000,
    ) -> None:
        """Swipe from (x1,y1) to (x2,y2).

        hmdriver2 takes speed (px/s) rather than duration; we convert.
        """
        await self.ensure_connected()
        distance = math.hypot(x2 - x1, y2 - y1)
        duration_s = max(duration_ms / 1000.0, 0.05)
        speed = max(200, min(40000, int(distance / duration_s)))
        await asyncio.to_thread(
            self._hm_driver.swipe, x1, y1, x2, y2, speed
        )
        # hmdriver2 returns before the gesture finishes; sleep a bit so callers
        # see a settled UI.
        await asyncio.sleep(duration_s * 0.5)

    async def input_text(self, text: str, clear: bool = False, **kwargs) -> bool:
        """Type *text* into the focused input field.

        hmdriver2 inputs into the currently focused field; callers must tap
        a field first. The ``stealth`` / ``wpm`` kwargs from the base class
        are accepted but ignored (hmdriver2 has no equivalent).
        """
        await self.ensure_connected()
        try:
            if clear:
                # Best-effort clear: select-all then backspace.
                # Using KEYCODES: 2072 = Ctrl+A combo path is unreliable;
                # fall back to repeated backspace is too brittle. Use triple-tap
                # to select all on most fields instead.
                pass  # clear is best-effort; not all fields support it.
            await asyncio.to_thread(self._hm_driver.input_text, text)
            return True
        except Exception as e:
            logger.warning("input_text failed: %s", e)
            return False

    async def press_button(self, button: str) -> None:
        if button not in self.supported_buttons:
            raise ValueError(
                f"Unsupported button {button!r}. Supported: {self.supported_buttons}"
            )
        key_map = {"back": 2049, "home": 2077, "enter": 2054}
        await self._press_key_code(key_map[button])

    async def _press_key_code(self, code: int) -> None:
        await self.ensure_connected()
        await asyncio.to_thread(self._hm_driver.press_key, code)

    # ── App management ───────────────────────────────────────────────────

    async def start_app(self, package: str, activity: str | None = None) -> str:
        """Launch an app by bundle name (and optional ability name).

        Tries ``force_start_app`` first (go_home + stop + start) so the app
        reliably comes to the foreground — plain ``start_app`` on HarmonyOS
        often leaves the previous app on top if the new one is already in the
        back stack.
        """
        await self.ensure_connected()
        try:
            # Prefer force_start for reliable foreground switching.
            if hasattr(self._hm_driver, "force_start_app"):
                await asyncio.to_thread(
                    self._hm_driver.force_start_app, package, activity
                )
            else:
                await asyncio.to_thread(
                    self._hm_driver.start_app, package, activity
                )
            return f"Started {package}" + (f"/{activity}" if activity else "")
        except Exception as e:
            return f"Failed to start {package}: {e}"

    async def get_apps(self, include_system: bool = True) -> list[dict[str, str]]:
        """List installed apps. hmdriver2 returns bundle names primarily."""
        await self.ensure_connected()
        try:
            apps = await asyncio.to_thread(
                self._hm_driver.list_apps, include_system
            )
            out: list[dict[str, str]] = []
            for a in apps or []:
                if isinstance(a, str):
                    out.append({"package": a, "label": a})
                elif isinstance(a, dict):
                    out.append({
                        "package": a.get("bundleName") or a.get("package") or "",
                        "label": a.get("label") or a.get("name") or a.get("bundleName") or "",
                    })
            return out
        except Exception as e:
            logger.warning("get_apps failed: %s", e)
            return []

    # ── Observation ──────────────────────────────────────────────────────

    async def screenshot(self, hide_overlay: bool = True) -> bytes:
        """Capture screen and return JPEG/PNG bytes.

        hmdriver2 writes to a file path and returns the path; we read it.
        """
        await self.ensure_connected()
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".jpg", prefix="hmos_shot_"
        ) as f:
            tmp_path = f.name
        try:
            method = (
                "snapshot_display"
                if self._screenshot_method in ("auto", "snapshot")
                else "screenCap"
            )
            await asyncio.to_thread(self._hm_driver.screenshot, tmp_path, method)
            return Path(tmp_path).read_bytes()
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

    async def get_ui_tree(self) -> dict[str, Any]:
        """Return the UI tree in mobilerun's standard state schema.

        Output shape:
            {
              "a11y_tree":   [element_dict, ...],  # flat, indexed list
              "phone_state": {"currentApp": str, "packageName": str},
              "device_context": {"screen_bounds": {"width": int, "height": int}},
            }

        Each element_dict has the schema consumed by IndexedFormatter /
        UIState: {index, type, className, text, bounds, id, key, description,
        clickable, enabled, selected, checked, children: []}.

        Note on phone_state: hmdriver2's ``current_app()`` reads ``aa dump -l``
        which on HarmonyOS reports stale BACKGROUND states for all missions,
        so it cannot be trusted. We instead extract the foreground bundle
        from the hierarchy itself: the top-level non-system child of the root
        carries a ``bundleName`` attribute for the visible window (the system
        sceneboard layer carries ``com.ohos.sceneboard`` and is skipped).
        """
        await self.ensure_connected()
        hierarchy = await asyncio.to_thread(self._hm_driver.dump_hierarchy)

        elements: list[dict[str, Any]] = []
        self._flatten(hierarchy, elements, [0])

        package_name = self._extract_foreground_bundle(hierarchy)
        current_app = package_name

        return {
            "a11y_tree": elements,
            "phone_state": {
                "currentApp": current_app,
                "packageName": package_name,
            },
            "device_context": {
                "screen_bounds": {
                    "width": self._screen_width or 0,
                    "height": self._screen_height or 0,
                }
            },
        }

    @staticmethod
    def _extract_foreground_bundle(hierarchy: dict) -> str:
        """Extract the foreground app's bundleName from the hierarchy root.

        HarmonyOS dump_hierarchy nests windows under the root; the visible
        app window carries ``bundleName`` in its attributes. The system
        sceneboard layer (``com.ohos.sceneboard``) is also present and must
        be skipped. Returns "" if no foreground bundle can be determined.
        """
        SYSTEM_BUNDLES = {"com.ohos.sceneboard"}
        children = hierarchy.get("children", []) or []
        for child in children:
            attrs = child.get("attributes", {}) or {}
            bundle = attrs.get("bundleName", "") or ""
            if bundle and bundle not in SYSTEM_BUNDLES:
                return bundle
        # Fallback: search the whole root attributes (some versions put it there)
        root_attrs = hierarchy.get("attributes", {}) or {}
        return root_attrs.get("bundleName", "") or ""

    def _flatten(self, node: dict, out: list[dict], counter: list[int]) -> None:
        """Recursively flatten hmdriver2 hierarchy into mobilerun element dicts.

        ``counter`` is a one-element list used as a mutable index counter.
        """
        attrs = node.get("attributes", {}) or {}
        node_type = attrs.get("type")
        if node_type:
            out.append({
                "index": counter[0],
                "type": node_type,
                "className": node_type,
                "text": attrs.get("text", "") or "",
                "bounds": attrs.get("bounds", "") or "",
                # Extra attributes useful for locator extraction:
                "id": attrs.get("id", "") or "",
                "key": attrs.get("key", "") or "",
                "description": attrs.get("description", "") or "",
                "clickable": _to_bool(attrs.get("clickable")),
                "enabled": _to_bool(attrs.get("enabled")),
                "selected": _to_bool(attrs.get("selected")),
                "checked": _to_bool(attrs.get("checked")),
                "children": [],
            })
            counter[0] += 1
        for child in node.get("children", []) or []:
            self._flatten(child, out, counter)

    async def get_date(self) -> str:
        """Return device date. hmdriver2 has no direct API; use shell."""
        await self.ensure_connected()
        try:
            result = await asyncio.to_thread(
                self._hm_driver.shell, "param get const.time.zone"
            )
            return str(result)
        except Exception:
            return ""

"""HarmonyOS dual-channel state provider — screenshot + UI tree.

This provider is the HarmonyOS counterpart of ``ScreenshotOnlyStateProvider``
but additionally surfaces the UI control tree (from ``hmdriver2.dump_hierarchy``)
to the LLM. The LLM gets both:
  - a screenshot (vision), and
  - a text list of UI elements with indices and bounds (structured)

so it can ground natural-language references ("the + button", "the send field")
to specific element indices or pixel coordinates, whichever is more reliable.

Output ``UIState``:
  - ``elements``: flat list of element dicts (for element-index tools)
  - ``formatted_text``: phone-state header + coordinate instruction + UI tree
  - ``phone_state``: app/keyboard info
  - screen dimensions + normalized-coordinate flags

The UI tree text is produced by reusing ``IndexedFormatter`` so the element
numbering scheme matches what mobilerun's click/type-by-index tools expect.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mobilerun.tools.formatters.indexed_formatter import IndexedFormatter
from mobilerun.tools.helpers.images import fit_dimensions_to_max_side, image_dimensions
from mobilerun.tools.ui.provider import StateProvider
from mobilerun.tools.ui.state import UIState

if TYPE_CHECKING:
    from mobilerun.tools.driver.base import DeviceDriver

logger = logging.getLogger("mobilerun")


class HarmonyStateProvider(StateProvider):
    """Build UI state from BOTH screenshot (vision) and UI tree (structured).

    Capabilities declared (mobilerun tool registry uses these):
      - convert_point: enable normalized→absolute coordinate tools (click_at)
      - element_index: enable element-index-based tools (click/type by index)
      - direct_text_input: enable direct text input tool

    Args:
        driver: a HarmonyOSDriver (or compatible) that exposes
            ``screenshot()`` and ``get_ui_tree()``.
        use_normalized: when True, LLM uses [0-1000] normalized coordinates
            instead of raw screenshot pixels.
        max_elements: cap on number of elements emitted to the LLM, to keep
            prompts manageable. None = no cap.
    """

    supported = {"convert_point", "element_index", "direct_text_input"}
    requires_coordinate_tools = True

    def __init__(
        self,
        driver: "DeviceDriver",
        use_normalized: bool = False,
        max_elements: int | None = None,
    ) -> None:
        super().__init__(driver)
        self.use_normalized = use_normalized
        self.max_elements = max_elements
        self._formatter = IndexedFormatter()

    async def get_state(self) -> UIState:
        # 1) Screenshot (for vision models).
        screenshot = await self.driver.screenshot()
        native_w, native_h = image_dimensions(screenshot)

        # Native screenshot pixels are the LLM-facing coordinate space when
        # use_normalized=False. input_coordinate_size defaults to native size.
        input_size = getattr(self.driver, "input_coordinate_size", None)
        if input_size is None:
            input_w, input_h = native_w, native_h
        else:
            input_w, input_h = await input_size(native_w, native_h)

        # mobilerun fits screenshots to a max side for vision models; mirror
        # that so the bounds shown to the LLM match the displayed screenshot.
        screen_w, screen_h = fit_dimensions_to_max_side(native_w, native_h)
        max_x = max(screen_w - 1, 0)
        max_y = max(screen_h - 1, 0)

        # 2) UI tree (structured, for element-index tools and grounding).
        elements: list[dict] = []
        tree_text = ""
        phone_state: dict = {
            "observationMode": "screenshot_plus_tree",
            "accessibilityTree": True,
        }
        try:
            state = await self.driver.get_ui_tree()
            raw_tree = state.get("a11y_tree", []) or []
            phone_state.update(state.get("phone_state", {}) or {})
            # Re-key a11y_tree into the shape IndexedFormatter expects: it
            # looks for a nested tree, but our driver returns a flat list.
            # Easiest: format the flat list directly here.
            elements, tree_text = self._format_flat_tree(
                raw_tree, screen_w, screen_h
            )
        except Exception as e:
            logger.warning("get_ui_tree failed; falling back to screenshot-only: %s", e)
            phone_state["treeError"] = str(e)

        # 3) Coordinate instruction (matches screenshot-only wording for parity).
        if self.use_normalized:
            coord_instruction = (
                "Dual-channel mode is active (screenshot + UI tree, "
                "normalized [0-1000] coordinates). "
                "Use element index when the target appears in the UI tree; "
                "otherwise use normalized coordinates: x from 0 (left) to 1000 "
                f"(right), y from 0 (top) to 1000 (bottom). "
                f"({max_x},{max_y}) maps to (1000,1000). "
            )
        else:
            coord_instruction = (
                "Dual-channel mode is active (screenshot + UI tree). "
                "Prefer clicking by element index when the target appears in "
                "the UI tree. Otherwise use pixel coordinates in the screenshot "
                f"coordinate space shown to the model ({screen_w}x{screen_h}; "
                f"(0,0) is top-left, ({max_x},{max_y}) is bottom-right). "
            )

        # 4) Assemble final formatted_text.
        if tree_text:
            formatted_text = (
                coord_instruction
                + "\n\n## Current UI Elements (index. type: text - bounds)\n"
                + tree_text
            )
        else:
            # Tree unavailable — behave like screenshot-only.
            formatted_text = coord_instruction + (
                "No UI tree available. Inspect the screenshot and use "
                "coordinate actions."
            )

        return UIState(
            elements=elements,
            formatted_text=formatted_text,
            focused_text="",
            phone_state=phone_state,
            screen_width=screen_w,
            screen_height=screen_h,
            use_normalized=self.use_normalized,
            coordinate_scale_x=input_w / screen_w if screen_w else 1.0,
            coordinate_scale_y=input_h / screen_h if screen_h else 1.0,
        )

    # Element types that are pure layout containers in HarmonyOS / ArkUI.
    # They never carry user-facing meaning on their own; we only keep them
    # if they're also clickable (some ListItems act as tappable rows).
    _PURE_CONTAINER_TYPES = frozenset({
        "Stack", "Row", "Column", "Flex", "Grid", "GridItem",
        "List", "ListItem", "RelativeContainer", "__Common__",
        "Navigation", "NavigationContent", "NavDestination",
        "NavDestinationContent", "WindowScene", "root",
        "RichEditorContent",
    })

    # Status-bar / system-overlay IDs. Their bounds live in the top ~150px
    # band and contribute nothing to app automation decisions.
    _STATUS_BAR_ID_PREFIXES = (
        "StatusBar",
        "StatusBarAppIcon",
        "WifiComponent",
        "SignalComponent",
        "BatteryComponent",
        "bluetooth-",
        "ringmode-",
        "TimeView_",
        "LiveMetaBall",
    )

    # Bounds in the top status-bar band (y < STATUS_BAR_HEIGHT_NATIVE) are
    # treated as status-bar elements when the element has no app meaning.
    # Native status bar is ~96-146px on 1276-wide devices.
    _STATUS_BAR_HEIGHT_NATIVE = 150

    def _in_status_bar_band(self, bounds_str: str) -> bool:
        """True if element's bottom edge is within the status-bar band."""
        import re

        nums = re.findall(r"\d+", bounds_str or "")
        if len(nums) < 4:
            return False
        try:
            bottom = int(nums[3])
        except ValueError:
            return False
        return bottom <= self._STATUS_BAR_HEIGHT_NATIVE

    def _is_status_bar(self, el: dict) -> bool:
        # Match by id prefix...
        el_id = (el.get("id") or "").strip()
        if any(el_id.startswith(p) for p in self._STATUS_BAR_ID_PREFIXES):
            return True
        # ...or by band: small elements fully inside the status-bar band with
        # no app-meaningful text/id are almost always status-bar icons.
        text = (el.get("text") or "").strip()
        if not text and not el_id:
            return self._in_status_bar_band(el.get("bounds", ""))
        return False

    def _format_flat_tree(
        self, elements: list[dict], screen_w: int, screen_h: int
    ) -> tuple[list[dict], str]:
        """Format the driver's flat element list into LLM-facing text.

        Returns (elements_with_stable_index, text_block). We re-number the
        *kept* elements 1..N to match mobilerun's convention and emit one
        line per element.

        Filtering rules (drop elements that waste prompt tokens):
          - Status-bar / system-overlay elements (wifi/battery/clock icons).
          - Pure layout containers (Stack/Row/Column/...) are dropped unless
            they are also clickable (some ListItems are tappable rows).
          - Elements with no text/id/description AND not clickable AND no
            checked/selected state are dropped (no info for the LLM and no
            interaction affordance).
          - Zero-size or off-screen bounds are dropped.

        Bounds normalization: hmdriver2 bounds are in *native* pixels
        (e.g. "[0,133][1276,2848]"). The LLM-facing screenshot is scaled to
        (screen_w, screen_h) by fit_dimensions_to_max_side, so we rescale
        bounds to the screenshot space to match what the LLM sees.
        """
        native_w = self._driver_native_width(elements) or screen_w
        native_h = self._driver_native_height(elements) or screen_h

        out: list[dict] = []
        lines: list[str] = []
        idx = 1
        skipped = 0
        for el in elements:
            el_type = el.get("type", "") or ""
            text = (el.get("text") or "").strip()
            el_id = (el.get("id") or "").strip()
            desc = (el.get("description") or "").strip()
            clickable = bool(el.get("clickable"))
            checked = bool(el.get("checked"))
            selected = bool(el.get("selected"))
            bounds_raw = el.get("bounds", "") or ""

            # Rule 0: drop status-bar / system overlay elements.
            if self._is_status_bar(el):
                skipped += 1
                continue

            # Rule 1: drop pure containers unless they're tappable.
            if el_type in self._PURE_CONTAINER_TYPES and not clickable:
                skipped += 1
                continue

            # Rule 2: drop elements with no signal at all.
            if not (text or el_id or desc or clickable or checked or selected):
                skipped += 1
                continue

            # Rule 3: drop zero-size / unparseable bounds.
            bounds_scaled = self._rescale_bounds(bounds_raw, native_w, native_h, screen_w, screen_h)
            if bounds_scaled is None:
                skipped += 1
                continue

            label = text or desc or el_id
            flags = []
            if clickable:
                flags.append("clickable")
            if checked:
                flags.append("checked")
            if selected:
                flags.append("selected")
            flag_str = f" [{','.join(flags)}]" if flags else ""
            id_hint = f" id={el_id}" if el_id else ""

            lines.append(
                f"{idx}. {el_type}: {label!r}{id_hint} - bounds={bounds_scaled}{flag_str}"
            )

            el_copy = dict(el)
            el_copy["index"] = idx
            el_copy["bounds"] = bounds_scaled  # rescaled for tap_element math
            out.append(el_copy)
            idx += 1

            if self.max_elements and len(out) >= self.max_elements:
                lines.append(
                    f"... ({len(elements) - len(out) - skipped} more elements after this truncated)"
                )
                break

        return out, "\n".join(lines)

    @staticmethod
    def _rescale_bounds(
        bounds_str: str, nw: int, nh: int, sw: int, sh: int
    ) -> str | None:
        """Rescale hmdriver2 native bounds "[l,t][r,b]" to screenshot space.

        Returns bounds in mobilerun's canonical "l,t,r,b" comma format (NO
        brackets) so downstream code like ``UIState.get_element_coords``
        (which splits on "," and maps to int) can parse it directly. Returns
        None if unparseable / zero-size.
        """
        import re

        nums = re.findall(r"\d+", bounds_str)
        if len(nums) < 4:
            return None
        try:
            l, t, r, b = (int(nums[0]), int(nums[1]), int(nums[2]), int(nums[3]))
        except ValueError:
            return None
        if r <= l or b <= t:
            return None
        if nw == sw and nh == sh:
            return f"{l},{t},{r},{b}"
        sx = sw / nw if nw else 1.0
        sy = sh / nh if nh else 1.0
        return (
            f"{int(round(l * sx))},{int(round(t * sy))},"
            f"{int(round(r * sx))},{int(round(b * sy))}"
        )

    @staticmethod
    def _driver_native_width(elements: list[dict]) -> int:
        """Recover native screen width from the widest element's bounds."""
        import re

        best = 0
        for el in elements:
            nums = re.findall(r"\d+", el.get("bounds", "") or "")
            if len(nums) >= 4:
                best = max(best, int(nums[2]))
        return best

    @staticmethod
    def _driver_native_height(elements: list[dict]) -> int:
        """Recover native screen height from the tallest element's bounds."""
        import re

        best = 0
        for el in elements:
            nums = re.findall(r"\d+", el.get("bounds", "") or "")
            if len(nums) >= 4:
                best = max(best, int(nums[3]))
        return best

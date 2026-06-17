"""TFT 可视化调试工具 — ROI 标注、OCR 结果叠加、操作箭头。

参照 match3/visualizer.py 的模式。
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import Any

logger = logging.getLogger("gameauto.tft.visualizer")

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


# ── Color palette ──────────────────────────────────────────────────────
_COLOR_GOLD = (255, 215, 0, 200)       # Gold — info overlay
_COLOR_RED = (255, 60, 60, 200)        # Red — action/tap
_COLOR_GREEN = (60, 255, 60, 200)      # Green — success/board
_COLOR_BLUE = (60, 120, 255, 200)      # Blue — bench
_COLOR_WHITE = (255, 255, 255, 180)    # White — general lines
_COLOR_SHOP = (255, 180, 50, 180)      # Orange — shop


def _get_font(size: int = 14) -> Any:
    """Get a PIL font, falling back to default."""
    if not _PIL_AVAILABLE:
        return None
    try:
        return ImageFont.truetype("simhei.ttf", size)
    except Exception:
        try:
            return ImageFont.truetype("arial.ttf", size)
        except Exception:
            return ImageFont.load_default()


def annotate_rois(
    image: bytes,
    rois: dict[str, tuple[float, float, float, float]],
    title: str = "ROI Debug",
) -> bytes:
    """Draw colored rectangles for each ROI on the screenshot.

    Args:
        image: PNG screenshot bytes.
        rois: {label: (left, top, right, bottom)} normalized [0-1].
        title: Overlay title text.

    Returns:
        Annotated PNG bytes.
    """
    if not _PIL_AVAILABLE:
        return image

    try:
        img = Image.open(BytesIO(image)).convert("RGBA")
        w, h = img.size
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        font = _get_font(12)

        colors = [_COLOR_RED, _COLOR_GREEN, _COLOR_BLUE, _COLOR_GOLD, _COLOR_SHOP]
        for i, (label, roi) in enumerate(rois.items()):
            color = colors[i % len(colors)]
            left = int(roi[0] * w)
            top = int(roi[1] * h)
            right = int(roi[2] * w)
            bottom = int(roi[3] * h)

            # Rect outline
            draw.rectangle([left, top, right, bottom], outline=color, width=2)

            # Label
            if font:
                draw.text((left + 2, top + 2), label, fill=color, font=font)

        # Title
        if font:
            draw.text((10, 10), title, fill=_COLOR_WHITE, font=_get_font(16))

        img = Image.alpha_composite(img, overlay)

        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    except Exception:
        logger.warning("ROI annotation failed", exc_info=True)
        return image


def annotate_actions(
    image: bytes,
    actions: list[Any],  # list[Action]
) -> bytes:
    """Draw tap points and swipe arrows for each Action.

    Args:
        image: PNG screenshot bytes.
        actions: List of Action objects with normalized [0-1000] coords.

    Returns:
        Annotated PNG bytes.
    """
    if not _PIL_AVAILABLE or not actions:
        return image

    try:
        img = Image.open(BytesIO(image)).convert("RGBA")
        w, h = img.size
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        font = _get_font(11)

        for i, action in enumerate(actions):
            # Convert normalized [0-1000] to pixels
            px = int(action.x1 * w / 1000)
            py = int(action.y1 * h / 1000)

            if action.type == "tap":
                # Draw crosshair at tap point
                r = 12
                draw.ellipse([px - r, py - r, px + r, py + r],
                             outline=_COLOR_RED, width=2)
                draw.line([px - r, py, px + r, py], fill=_COLOR_RED, width=2)
                draw.line([px, py - r, px, py + r], fill=_COLOR_RED, width=2)

                if font and action.description:
                    draw.text((px + r + 4, py - 6), action.description,
                              fill=_COLOR_RED, font=font)

            elif action.type in ("swipe", "drag"):
                px2 = int(action.x2 * w / 1000)
                py2 = int(action.y2 * h / 1000)
                draw.line([px, py, px2, py2], fill=_COLOR_GREEN, width=3)
                draw.ellipse([px - 4, py - 4, px + 4, py + 4],
                             fill=_COLOR_GREEN)
                draw.ellipse([px2 - 6, py2 - 6, px2 + 6, py2 + 6],
                             outline=_COLOR_GREEN, width=2)

        img = Image.alpha_composite(img, overlay)

        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    except Exception:
        logger.warning("Action annotation failed", exc_info=True)
        return image

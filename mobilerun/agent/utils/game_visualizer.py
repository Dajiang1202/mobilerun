"""Swipe visualization and game-log persistence for FastGameAgent.

Draws start/end markers and an arrow on the screenshot, then saves the
annotated image alongside a reasoning log file into a timestamped
directory under ``game_logs/``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("mobilerun")

START_COLOR = (220, 38, 38)  # red
END_COLOR = (34, 197, 94)  # green
ARROW_COLOR = (234, 179, 8)  # amber
CIRCLE_RADIUS = 18
ARROW_WIDTH = 4


def annotate_swipe(
    screenshot_bytes: bytes,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
) -> bytes:
    """在截图上绘制滑动起止标记和箭头，用于游戏操作可视化调试。

    Args:
        screenshot_bytes: PNG 或 JPEG 截图字节流。
        x1, y1: 滑动起点（绝对像素坐标）。
        x2, y2: 滑动终点（绝对像素坐标）。

    Returns:
        标注后的 PNG 图片字节流。
    """
    img = Image.open(BytesIO(screenshot_bytes)).convert("RGBA")

    # 叠加层：半透明圆形标记 + 箭头线
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    r = CIRCLE_RADIUS
    draw.ellipse((x1 - r, y1 - r, x1 + r, y1 + r), fill=(*START_COLOR, 80), outline=START_COLOR, width=3)
    draw.ellipse((x2 - r, y2 - r, x2 + r, y2 + r), fill=(*END_COLOR, 80), outline=END_COLOR, width=3)

    # Arrow line
    draw.line((x1, y1, x2, y2), fill=(*ARROW_COLOR, 200), width=ARROW_WIDTH)

    # Arrowhead at end point
    _draw_arrowhead(draw, x1, y1, x2, y2, size=14, color=ARROW_COLOR)

    # Small dot at exact start
    draw.ellipse((x1 - 4, y1 - 4, x1 + 4, y1 + 4), fill=START_COLOR)
    # Small dot at exact end
    draw.ellipse((x2 - 4, y2 - 4, x2 + 4, y2 + 4), fill=END_COLOR)

    img = Image.alpha_composite(img, overlay).convert("RGB")

    # Draw coordinate labels
    draw = ImageDraw.Draw(img)
    font = _load_label_font()
    draw.text((x1 + r + 6, y1 - r - 8), f"S({x1},{y1})", fill=START_COLOR, font=font)
    draw.text((x2 + r + 6, y2 - r - 8), f"E({x2},{y2})", fill=END_COLOR, font=font)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def save_game_log(
    annotated_img_bytes: bytes,
    thought_text: str,
    tool_name: str,
    tool_params: dict,
    logs_dir: str = "game_logs",
) -> Optional[str]:
    """Save annotated screenshot and reasoning log to disk.

    Args:
        annotated_img_bytes: Annotated PNG bytes from ``annotate_swipe``.
        thought_text: The VLM's reasoning/thought output.
        tool_name: Name of the tool that was executed.
        tool_params: Parameters passed to the tool.
        logs_dir: Base directory for game logs (relative or absolute).

    Returns:
        Path to the log directory for this step, or None on failure.
    """
    try:
        base = Path(logs_dir)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        step_dir = base / timestamp
        step_dir.mkdir(parents=True, exist_ok=True)

        # Annotated screenshot
        img_path = step_dir / "annotated_swipe.png"
        img_path.write_bytes(annotated_img_bytes)
        logger.debug(f"Game log image saved: {img_path}")

        # Reasoning text
        txt_path = step_dir / "reasoning.txt"
        params_str = "\n".join(f"  {k}: {v}" for k, v in tool_params.items())
        content = (
            f"Timestamp: {timestamp}\n"
            f"Tool: {tool_name}\n"
            f"Parameters:\n{params_str}\n"
            f"{'─' * 60}\n"
            f"VLM Reasoning:\n{thought_text}\n"
        )
        txt_path.write_text(content, encoding="utf-8")
        logger.debug(f"Game log reasoning saved: {txt_path}")

        return str(step_dir)
    except Exception:
        logger.warning("Failed to save game log", exc_info=True)
        return None


def _draw_arrowhead(
    draw: ImageDraw.ImageDraw,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    size: int = 14,
    color: tuple = ARROW_COLOR,
) -> None:
    """Draw a triangular arrowhead at (x2, y2) pointing from (x1, y1)."""
    import math

    angle = math.atan2(y2 - y1, x2 - x1)
    spread = math.pi / 6

    p1 = (x2 - int(size * math.cos(angle - spread)), y2 - int(size * math.sin(angle - spread)))
    p2 = (x2 - int(size * math.cos(angle + spread)), y2 - int(size * math.sin(angle + spread)))

    draw.polygon([(x2, y2), p1, p2], fill=(*color, 220))


def _load_label_font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a reasonable label font, falling back to default."""
    try:
        return ImageFont.truetype("arial.ttf", 14)
    except (OSError, IOError):
        pass
    try:
        return ImageFont.truetype("DejaVuSans.ttf", 14)
    except (OSError, IOError):
        pass
    return ImageFont.load_default()

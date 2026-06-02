"""Swipe visualization, board-grid overlay, and game-log persistence for FastGameAgent.

Draws start/end markers and an arrow on the screenshot, renders the VLM-recognized
board grid with tile labels, then saves the annotated image alongside a reasoning
log file into a timestamped directory under ``game_logs/``.
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
GRID_COLOR = (255, 255, 255, 120)  # white, semi-transparent
EMPTY_COLOR = (128, 128, 128, 100)  # gray overlay for empty cells
BLOCKED_COLOR = (180, 60, 60, 140)  # dark-red overlay for blocked cells
LABEL_COLOR = (255, 255, 255)  # white text
CIRCLE_RADIUS = 18
ARROW_WIDTH = 4

# Multi-swipe color schemes: (start_rgb, end_rgb, arrow_rgb) for up to 3 steps
_MULTI_COLORS = [
    (START_COLOR, END_COLOR, ARROW_COLOR),                          # step 1: red/green/amber
    ((59, 130, 246), (168, 85, 247), (6, 182, 212)),                # step 2: blue/purple/cyan
    ((251, 146, 60), (52, 211, 153), (250, 204, 21)),              # step 3: orange/emerald/yellow
]


def annotate_board(
    screenshot_bytes: bytes,
    board: dict,
    show_tiles: bool = True,
) -> bytes:
    """Overlay the recognized board grid and tile labels onto the screenshot.

    Draws cell boundary lines, fills empty/blocked cells with a coloured
    translucent overlay, and writes each tile's label string.

    Args:
        screenshot_bytes: PNG or JPEG screenshot bytes in **native** resolution.
        board: Board JSON dict with ``rows``, ``cols``, ``board_left``,
            ``board_top``, ``board_right``, ``board_bottom`` (all [0-1000]
            normalized), and ``tiles`` (2-D list of label strings).
        show_tiles: When True, render the tile-type label text inside each cell.

    Returns:
        Annotated PNG image bytes.
    """
    img = Image.open(BytesIO(screenshot_bytes)).convert("RGBA")
    native_w, native_h = img.size

    rows: int = len(board["tiles"])
    cols: int = len(board["tiles"][0]) if rows else 0
    if rows == 0 or cols == 0:
        return screenshot_bytes

    bl = float(board.get("board_left", 0)) * native_w / 1000
    bt = float(board.get("board_top", 0)) * native_h / 1000
    br = float(board.get("board_right", 1000)) * native_w / 1000
    bb = float(board.get("board_bottom", 1000)) * native_h / 1000
    cell_w = (br - bl) / cols
    cell_h = (bb - bt) / rows

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _load_cell_font(int(min(cell_w, cell_h) * 0.35))

    for r in range(rows):
        for c in range(cols):
            x0 = bl + c * cell_w
            y0 = bt + r * cell_h
            x1 = x0 + cell_w
            y1 = y0 + cell_h

            label = (board["tiles"][r][c] if r < len(board["tiles"]) and c < len(board["tiles"][r])
                     else "").strip().lower()

            # Highlight empty / blocked cells
            if label == "empty":
                draw.rectangle((x0, y0, x1, y1), fill=EMPTY_COLOR)
            elif label == "blocked":
                draw.rectangle((x0, y0, x1, y1), fill=BLOCKED_COLOR)
                # Draw cross pattern to make blocked visually obvious
                draw.line((x0, y0, x1, y1), fill=(200, 60, 60, 180), width=2)
                draw.line((x1, y0, x0, y1), fill=(200, 60, 60, 180), width=2)

            # Cell border
            draw.rectangle((x0, y0, x1, y1), outline=GRID_COLOR, width=1)

            # Tile label text (centered) — abbreviate for readability
            if show_tiles and label not in ("", "empty"):
                abbr = _abbreviate(label)
                _draw_centered_text(draw, abbr, x0, y0, x1, y1, font, LABEL_COLOR)

    img = Image.alpha_composite(img, overlay).convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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


def annotate_multi_swipe(
    screenshot_bytes: bytes,
    swipes: list,
) -> bytes:
    """Draw multiple swipe annotations on the same screenshot with distinct colors.

    Args:
        screenshot_bytes: PNG or JPEG bytes.
        swipes: List of (x1, y1, x2, y2) tuples, one per swipe.
                Each represents start/end in native pixel coordinates.

    Returns:
        Annotated PNG bytes with all swipes drawn.
    """
    img = Image.open(BytesIO(screenshot_bytes)).convert("RGBA")

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    r = CIRCLE_RADIUS

    for idx, (x1, y1, x2, y2) in enumerate(swipes):
        start_c, end_c, arrow_c = _MULTI_COLORS[idx % len(_MULTI_COLORS)]

        # Start and end markers
        draw.ellipse((x1 - r, y1 - r, x1 + r, y1 + r), fill=(*start_c, 80), outline=start_c, width=3)
        draw.ellipse((x2 - r, y2 - r, x2 + r, y2 + r), fill=(*end_c, 80), outline=end_c, width=3)

        # Arrow line
        draw.line((x1, y1, x2, y2), fill=(*arrow_c, 200), width=ARROW_WIDTH)

        # Arrowhead at end point
        _draw_arrowhead(draw, x1, y1, x2, y2, size=14, color=arrow_c)

        # Small dots at exact positions
        draw.ellipse((x1 - 4, y1 - 4, x1 + 4, y1 + 4), fill=start_c)
        draw.ellipse((x2 - 4, y2 - 4, x2 + 4, y2 + 4), fill=end_c)

    img = Image.alpha_composite(img, overlay).convert("RGB")

    # Draw step-numbered labels
    draw = ImageDraw.Draw(img)
    font = _load_label_font()
    for idx, (x1, y1, x2, y2) in enumerate(swipes):
        start_c, end_c, _arrow_c = _MULTI_COLORS[idx % len(_MULTI_COLORS)]
        step_num = idx + 1
        draw.text((x1 + r + 6, y1 - r - 8), f"S{step_num}({x1},{y1})", fill=start_c, font=font)
        draw.text((x2 + r + 6, y2 - r - 8), f"E{step_num}({x2},{y2})", fill=end_c, font=font)

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


def _load_cell_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a font sized for tile labels inside grid cells."""
    try:
        return ImageFont.truetype("arial.ttf", max(size, 8))
    except (OSError, IOError):
        pass
    try:
        return ImageFont.truetype("DejaVuSans.ttf", max(size, 8))
    except (OSError, IOError):
        pass
    return ImageFont.load_default()


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    x0: float, y0: float,
    x1: float, y1: float,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    color: tuple,
) -> None:
    """Draw text centered within the bounding box (x0,y0)-(x1,y1)."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    # Clamp label to fit inside cell — truncate with ellipsis if needed
    cell_w = x1 - x0
    if tw > cell_w - 4:
        while tw > cell_w - 4 and len(text) > 1:
            text = text[:-1]
            bbox = draw.textbbox((0, 0), text + "…", font=font)
            tw = bbox[2] - bbox[0]
        text = text + "…"
    cx = (x0 + x1) / 2 - tw / 2
    cy = (y0 + y1) / 2 - th / 2
    # Draw text shadow for readability
    draw.text((cx + 1, cy + 1), text, fill=(0, 0, 0), font=font)
    draw.text((cx, cy), text, fill=color, font=font)


def _abbreviate(label: str) -> str:
    """Convert a tile label to initials for compact display.

    ``color_shape`` → ``CS`` (first letter of each part, uppercased).
    Single-word labels → first letter uppercased.
    """
    parts = label.split("_")
    return "".join(p[0].upper() for p in parts if p)

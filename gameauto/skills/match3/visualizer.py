"""Board grid overlay and swipe visualization for match-3 games.

Adapted from mobilerun/agent/utils/game_visualizer.py.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

START_COLOR = (220, 38, 38)
END_COLOR = (34, 197, 94)
ARROW_COLOR = (234, 179, 8)
GRID_COLOR = (255, 255, 255, 120)
EMPTY_COLOR = (128, 128, 128, 100)
BLOCKED_COLOR = (180, 60, 60, 140)
LABEL_COLOR = (255, 255, 255)
CIRCLE_RADIUS = 18
ARROW_WIDTH = 4

_MULTI_COLORS = [
    (START_COLOR, END_COLOR, ARROW_COLOR),
    ((59, 130, 246), (168, 85, 247), (6, 182, 212)),
    ((251, 146, 60), (52, 211, 153), (250, 204, 21)),
]


def annotate_board(screenshot_bytes: bytes, board: dict, show_tiles: bool = True) -> bytes:
    """Overlay recognized board grid onto the screenshot."""
    img = Image.open(BytesIO(screenshot_bytes)).convert("RGBA")
    native_w, native_h = img.size

    tiles = board.get("tiles", [])
    rows = len(tiles)
    cols = len(tiles[0]) if rows else 0
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

            label = (tiles[r][c] if r < len(tiles) and c < len(tiles[r]) else "").strip().lower()

            if label == "empty":
                draw.rectangle((x0, y0, x1, y1), fill=EMPTY_COLOR)
            elif label == "blocked":
                draw.rectangle((x0, y0, x1, y1), fill=BLOCKED_COLOR)
                draw.line((x0, y0, x1, y1), fill=(200, 60, 60, 180), width=2)
                draw.line((x1, y0, x0, y1), fill=(200, 60, 60, 180), width=2)

            draw.rectangle((x0, y0, x1, y1), outline=GRID_COLOR, width=1)

            if show_tiles and label not in ("", "empty"):
                abbr = _abbreviate(label)
                _draw_centered_text(draw, abbr, x0, y0, x1, y1, font, LABEL_COLOR)

    img = Image.alpha_composite(img, overlay).convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def annotate_swipe(screenshot_bytes: bytes, x1: int, y1: int, x2: int, y2: int) -> bytes:
    """Draw single swipe arrow on screenshot."""
    img = Image.open(BytesIO(screenshot_bytes)).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    r = CIRCLE_RADIUS

    draw.ellipse((x1 - r, y1 - r, x1 + r, y1 + r), fill=(*START_COLOR, 80), outline=START_COLOR, width=3)
    draw.ellipse((x2 - r, y2 - r, x2 + r, y2 + r), fill=(*END_COLOR, 80), outline=END_COLOR, width=3)
    draw.line((x1, y1, x2, y2), fill=(*ARROW_COLOR, 200), width=ARROW_WIDTH)
    _draw_arrowhead(draw, x1, y1, x2, y2, size=14, color=ARROW_COLOR)
    draw.ellipse((x1 - 4, y1 - 4, x1 + 4, y1 + 4), fill=START_COLOR)
    draw.ellipse((x2 - 4, y2 - 4, x2 + 4, y2 + 4), fill=END_COLOR)

    img = Image.alpha_composite(img, overlay).convert("RGB")
    draw = ImageDraw.Draw(img)
    font = _load_label_font()
    draw.text((x1 + r + 6, y1 - r - 8), f"S({x1},{y1})", fill=START_COLOR, font=font)
    draw.text((x2 + r + 6, y2 - r - 8), f"E({x2},{y2})", fill=END_COLOR, font=font)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def annotate_multi_swipe(screenshot_bytes: bytes, swipes: list) -> bytes:
    """Draw multiple swipe arrows with distinct colors."""
    img = Image.open(BytesIO(screenshot_bytes)).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    r = CIRCLE_RADIUS

    for idx, (x1, y1, x2, y2) in enumerate(swipes):
        start_c, end_c, arrow_c = _MULTI_COLORS[idx % len(_MULTI_COLORS)]
        draw.ellipse((x1 - r, y1 - r, x1 + r, y1 + r), fill=(*start_c, 80), outline=start_c, width=3)
        draw.ellipse((x2 - r, y2 - r, x2 + r, y2 + r), fill=(*end_c, 80), outline=end_c, width=3)
        draw.line((x1, y1, x2, y2), fill=(*arrow_c, 200), width=ARROW_WIDTH)
        _draw_arrowhead(draw, x1, y1, x2, y2, size=14, color=arrow_c)
        draw.ellipse((x1 - 4, y1 - 4, x1 + 4, y1 + 4), fill=start_c)
        draw.ellipse((x2 - 4, y2 - 4, x2 + 4, y2 + 4), fill=end_c)

    img = Image.alpha_composite(img, overlay).convert("RGB")
    draw = ImageDraw.Draw(img)
    font = _load_label_font()
    for idx, (x1, y1, x2, y2) in enumerate(swipes):
        start_c, end_c, _ = _MULTI_COLORS[idx % len(_MULTI_COLORS)]
        step_num = idx + 1
        draw.text((x1 + r + 6, y1 - r - 8), f"S{step_num}({x1},{y1})", fill=start_c, font=font)
        draw.text((x2 + r + 6, y2 - r - 8), f"E{step_num}({x2},{y2})", fill=end_c, font=font)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── helpers ──────────────────────────────────────────────────────────

def _draw_arrowhead(draw, x1, y1, x2, y2, size=14, color=ARROW_COLOR):
    import math
    angle = math.atan2(y2 - y1, x2 - x1)
    spread = math.pi / 6
    p1 = (x2 - int(size * math.cos(angle - spread)), y2 - int(size * math.sin(angle - spread)))
    p2 = (x2 - int(size * math.cos(angle + spread)), y2 - int(size * math.sin(angle + spread)))
    draw.polygon([(x2, y2), p1, p2], fill=(*color, 220))


def _draw_centered_text(draw, text, x0, y0, x1, y1, font, color):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    cell_w = x1 - x0
    while tw > cell_w - 4 and len(text) > 1:
        text = text[:-1]
        bbox = draw.textbbox((0, 0), text + "...", font=font)
        tw = bbox[2] - bbox[0]
        text = text + "..."
    cx = (x0 + x1) / 2 - tw / 2
    cy = (y0 + y1) / 2 - th / 2
    draw.text((cx + 1, cy + 1), text, fill=(0, 0, 0), font=font)
    draw.text((cx, cy), text, fill=color, font=font)


def _abbreviate(label: str) -> str:
    parts = label.split("_")
    return "".join(p[0].upper() for p in parts if p)


def _load_label_font():
    try:
        return ImageFont.truetype("arial.ttf", 14)
    except (OSError, IOError):
        pass
    try:
        return ImageFont.truetype("DejaVuSans.ttf", 14)
    except (OSError, IOError):
        pass
    return ImageFont.load_default()


def _load_cell_font(size):
    try:
        return ImageFont.truetype("arial.ttf", max(size, 8))
    except (OSError, IOError):
        pass
    try:
        return ImageFont.truetype("DejaVuSans.ttf", max(size, 8))
    except (OSError, IOError):
        pass
    return ImageFont.load_default()

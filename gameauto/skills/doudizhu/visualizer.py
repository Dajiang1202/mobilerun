"""DouDiZhu visualization — button/card/click annotation with color support."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

# Button color mapping (VLM color → RGBA outline, RGBA fill)
BUTTON_COLORS = {
    "gold":   ((234, 179, 8, 220),  (234, 179, 8, 50)),    # golden/yellow
    "blue":   ((59, 130, 246, 200), (59, 130, 246, 30)),    # blue
    "green":  ((34, 197, 94, 220),  (34, 197, 94, 50)),     # green
    "grey":   ((128, 128, 128, 160),(128, 128, 128, 25)),   # grey
}
DEFAULT_BUTTON_COLOR = BUTTON_COLORS["blue"]

CARD_COLOR = (59, 130, 246, 200)        # hand cards: blue
CARD_FILL = (59, 130, 246, 30)
CLICK_COLORS = [(220, 38, 38), (234, 179, 8), (34, 197, 94), (168, 85, 247)]
TEXT_COLOR = (255, 255, 255)
CIRCLE_RADIUS = 32
BUTTON_BOX_W = 90   # button box half-width
BUTTON_BOX_H = 28   # button box half-height


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    chinese_fonts = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
    ]
    for path in chinese_fonts:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    try:
        return ImageFont.truetype("arial.ttf", size)
    except (OSError, IOError):
        pass
    return ImageFont.load_default()


def annotate_game_state(screenshot_bytes: bytes, game_state: dict) -> bytes:
    """Overlay buttons (color-coded), hand cards (blue + suit+value) on the screenshot."""
    img = Image.open(BytesIO(screenshot_bytes)).convert("RGBA")
    native_w, native_h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _load_font(24)
    small_font = _load_font(16)

    # ── 1. Buttons: color-coded by VLM-reported color ──────────────────
    for btn in game_state.get("buttons", []):
        x = int(btn["x"] * native_w / 1000)
        y = int(btn["y"] * native_h / 1000)
        color_key = btn.get("color", "blue")
        box_color, box_fill = BUTTON_COLORS.get(color_key, DEFAULT_BUTTON_COLOR)

        x0, y0 = x - BUTTON_BOX_W, y - BUTTON_BOX_H
        x1, y1 = x + BUTTON_BOX_W, y + BUTTON_BOX_H
        draw.rectangle((x0, y0, x1, y1), fill=box_fill, outline=box_color, width=3)

        # Button text centered inside box
        label = btn.get("text", "?")
        _draw_centered(draw, label, x, y - 4, font, box_color[:3])

        # Color label below box
        color_label = color_key
        _draw_centered(draw, color_label, x, y + BUTTON_BOX_H + 12, small_font, box_color[:3])

    # ── 2. Hand cards: blue dot + suit+value below ─────────────────────
    cards = game_state.get("hand_cards", [])
    for card in cards:
        x = int(card["x"] * native_w / 1000)
        y = int(card["y"] * native_h / 1000)
        r = 14
        draw.ellipse((x - r, y - r, x + r, y + r), fill=CARD_FILL, outline=CARD_COLOR, width=2)

        # Card label: H3, SA, etc.
        suit = card.get("suit", "?")
        val = str(card.get("value", "?"))
        suit_sym = {"hearts": "H", "spades": "S", "diamonds": "D", "clubs": "C", "joker": "J"}.get(suit, "?")
        label = f"{suit_sym}{val}"
        _draw_centered(draw, label, x, y + r + 12, small_font, CARD_COLOR[:3])

    img = Image.alpha_composite(img, overlay).convert("RGB")

    # ── 3. Corner info bar ─────────────────────────────────────────────
    draw = ImageDraw.Draw(img)
    big_font = _load_font(32)
    line2_font = _load_font(20)

    screen_type = game_state.get("screen_type", "?")
    n_btns = len(game_state.get("buttons", []))
    n_cards = len(cards)

    # Black bar at top
    draw.rectangle((0, 0, img.width, 80), fill=(0, 0, 0, 200))
    info = f"Screen: {screen_type}  |  Buttons: {n_btns}  |  Cards: {n_cards}"
    draw.text((10, 6), info, fill=TEXT_COLOR, font=big_font)

    # Button text summary
    btn_texts = [b.get("text", "?") for b in game_state.get("buttons", [])]
    if btn_texts:
        draw.text((10, 46), f"Buttons: {', '.join(btn_texts)}", fill=(255, 200, 50), font=line2_font)
    else:
        draw.text((10, 46), "No buttons visible", fill=(150, 150, 150), font=line2_font)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def annotate_clicks(screenshot_bytes: bytes, actions: list) -> bytes:
    """Overlay numbered click circles with action descriptions."""
    img = Image.open(BytesIO(screenshot_bytes)).convert("RGBA")
    native_w, native_h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _load_font(24)
    desc_font = _load_font(18)

    for idx, action in enumerate(actions):
        if action.x1 is None or action.y1 is None:
            continue
        x = int(action.x1 * native_w / 1000)
        y = int(action.y1 * native_h / 1000)
        color = CLICK_COLORS[idx % len(CLICK_COLORS)]
        r = CIRCLE_RADIUS

        # Numbered circle
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(*color, 200), outline=color, width=4)
        num_text = str(idx + 1)
        bbox = draw.textbbox((0, 0), num_text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((x - tw // 2, y - th // 2), num_text, fill=TEXT_COLOR, font=font)

        # Description below circle
        desc = action.description or ""
        if len(desc) < 30:
            _draw_centered(draw, desc, x, y + r + 22, desc_font, (255, 255, 255))

    img = Image.alpha_composite(img, overlay).convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── helpers ──────────────────────────────────────────────────────────

def _draw_centered(draw, text, x, y, font, color):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    cx, cy = x - tw // 2, y - th // 2
    draw.text((cx + 1, cy + 1), text, fill=(0, 0, 0), font=font)
    draw.text((cx, cy), text, fill=color, font=font)

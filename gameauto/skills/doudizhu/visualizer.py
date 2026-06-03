"""DouDiZhu visualization — annotate buttons, cards, and click sequence."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

# Colors
BUTTON_COLOR = (34, 197, 94, 200)
BUTTON_FILL = (34, 197, 94, 50)
CARD_COLOR = (59, 130, 246, 200)
CARD_FILL = (59, 130, 246, 30)
CLICK_COLORS = [(220, 38, 38), (234, 179, 8), (34, 197, 94)]
TEXT_COLOR = (255, 255, 255)
CIRCLE_RADIUS = 30
BUTTON_BOX_PADDING = 16


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """Load a font that supports Chinese characters. Tries common Chinese fonts first."""
    chinese_fonts = [
        "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
        "C:/Windows/Fonts/simhei.ttf",      # 黑体
        "C:/Windows/Fonts/simsun.ttc",      # 宋体
        "C:/Windows/Fonts/msyhbd.ttc",      # 微软雅黑 Bold
    ]
    for path in chinese_fonts:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    # Fallback: arial then default
    try:
        return ImageFont.truetype("arial.ttf", size)
    except (OSError, IOError):
        pass
    return ImageFont.load_default()


def annotate_game_state(screenshot_bytes: bytes, game_state: dict) -> bytes:
    """Overlay buttons (green boxes) and hand cards (blue dots + index) on screenshot.

    Card labels shown as simple index numbers (1, 2, 3...) — no suit needed.
    """
    img = Image.open(BytesIO(screenshot_bytes)).convert("RGBA")
    native_w, native_h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _load_font(24)
    small_font = _load_font(18)

    # ── Buttons: green box + label ────────────────────────────────────
    for btn in game_state.get("buttons", []):
        x = int(btn["x"] * native_w / 1000)
        y = int(btn["y"] * native_h / 1000)
        pw = BUTTON_BOX_PADDING * 5  # wider for Chinese text
        x0, y0 = x - pw, y - BUTTON_BOX_PADDING
        x1, y1 = x + pw, y + BUTTON_BOX_PADDING
        draw.rectangle((x0, y0, x1, y1), fill=BUTTON_FILL, outline=BUTTON_COLOR, width=3)
        label = btn.get("text", "?")
        enabled = btn.get("enabled", True)
        # Disabled buttons: dimmer outline
        bcolor = BUTTON_COLOR if enabled else (128, 128, 128, 150)
        bfill = BUTTON_FILL if enabled else (128, 128, 128, 30)
        draw.rectangle((x0, y0, x1, y1), fill=bfill, outline=bcolor, width=3)
        _draw_centered(draw, label, x, y - BUTTON_BOX_PADDING - 12, font, bcolor[:3])
        # Show enabled/disabled for "提示"
        if btn.get("text") == "提示":
            status = "ON" if enabled else "OFF"
            _draw_centered(draw, status, x, y - BUTTON_BOX_PADDING - 38, small_font,
                           (34, 197, 94) if enabled else (200, 60, 60))

    # ── Hand cards: blue dot + suit+value ─────────────────────────────
    cards = game_state.get("hand_cards", [])
    for card in cards:
        x = int(card["x"] * native_w / 1000)
        y = int(card["y"] * native_h / 1000)
        r = 14
        draw.ellipse((x - r, y - r, x + r, y + r), fill=CARD_FILL, outline=CARD_COLOR, width=2)
        label = f"{_suit(card.get('suit',''))}{card.get('value','?')}"
        _draw_centered(draw, label, x, y - 22, small_font, CARD_COLOR[:3])

    # ── Last played cards (on table) ──────────────────────────────────
    last_played = game_state.get("last_played", [])
    if last_played:
        draw.text((10, 60), f"Last played: {' '.join(_suit(c.get('suit',''))+c.get('value','?') for c in last_played)}",
                  fill=(255, 200, 50), font=small_font)

    img = Image.alpha_composite(img, overlay).convert("RGB")

    # ── Corner info: phase + counts ────────────────────────────────────
    draw = ImageDraw.Draw(img)
    big_font = _load_font(36)
    phase = game_state.get("phase", "?")
    n_btns = len(game_state.get("buttons", []))
    n_cards = len(cards)
    info = f"Phase: {phase} | Buttons: {n_btns} | Cards: {n_cards}"
    # Black background bar for readability
    draw.rectangle((0, 0, img.width, 50), fill=(0, 0, 0, 180))
    draw.text((10, 6), info, fill=(255, 255, 255), font=big_font)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def annotate_clicks(screenshot_bytes: bytes, actions: list) -> bytes:
    """Overlay numbered click circles showing action sequence.

    Step 1 = red, Step 2 = amber, Step 3 = green.
    """
    img = Image.open(BytesIO(screenshot_bytes)).convert("RGBA")
    native_w, native_h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _load_font(24)

    for idx, action in enumerate(actions):
        if action.x1 is None or action.y1 is None:
            continue
        x = int(action.x1 * native_w / 1000)
        y = int(action.y1 * native_h / 1000)
        color = CLICK_COLORS[idx % len(CLICK_COLORS)]
        r = CIRCLE_RADIUS

        draw.ellipse((x - r, y - r, x + r, y + r), fill=(*color, 200), outline=color, width=4)
        num_text = str(idx + 1)
        bbox = draw.textbbox((0, 0), num_text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text((x - tw // 2, y - th // 2), num_text, fill=TEXT_COLOR, font=font)
        # Action label below circle
        desc = action.description or ""
        if desc and len(desc) < 20:
            _draw_centered(draw, desc, x, y + r + 22, _load_font(18), (255, 255, 255))

    img = Image.alpha_composite(img, overlay).convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── helpers ──────────────────────────────────────────────────────────

def _draw_centered(draw, text, x, y, font, color):
    """Draw centered text with shadow."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    cx = x - tw // 2
    cy = y - th // 2
    draw.text((cx + 1, cy + 1), text, fill=(0, 0, 0), font=font)
    draw.text((cx, cy), text, fill=color, font=font)


def _suit(suit: str) -> str:
    return {"hearts": "H", "spades": "S", "diamonds": "D", "clubs": "C"}.get(suit or "", "?")

"""天天象棋可视化 — 棋盘网格 + 红黑棋子 + 空位标记 + 走法箭头。"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont


def format_board_matrix(pieces: list[dict]) -> str:
    """把 pieces 渲染成 10×9 文本矩阵(黑方顶在上, 与屏幕方向一致), 供日志输出。

    每格: 红方 "r{字}" / 黑方 "b{字}" / 空位 " · "。比打印整段 JSON 紧凑得多。
    """
    grid: dict[tuple[int, int], tuple[str, str]] = {}
    for p in pieces:
        bp = p.get("board_pos", {})
        col, row = bp.get("col"), bp.get("row")
        if col and row:
            grid[(int(col), int(row))] = (str(p.get("piece", "?")), str(p.get("side", "?")))
    lines = []
    for r in range(10, 0, -1):  # row10(黑方顶) 在第一行
        cells = []
        for c in range(1, 10):
            g = grid.get((c, r))
            if g is None:
                cells.append(" · ")
            else:
                s = "r" if g[1] == "red" else "b"
                cells.append(f"{s}{g[0]}")
        lines.append(" ".join(cells))
    return "\n".join(lines)

# Colors
GRID_COLOR = (160, 120, 80, 150)
RIVER_COLOR = (160, 120, 80, 100)
RED_PIECE_OUTLINE = (220, 50, 50, 240)       # 红方外圈
RED_PIECE_FILL = (220, 50, 50, 50)            # 红方填充
RED_TEXT = (220, 50, 50)                       # 红方文字
BLACK_PIECE_OUTLINE = (30, 30, 30, 240)       # 黑方外圈
BLACK_PIECE_FILL = (30, 30, 30, 50)            # 黑方填充
BLACK_TEXT = (30, 30, 30)                       # 黑方文字
EMPTY_DOT = (120, 120, 120, 100)               # 空位圆点
BUTTON_OUTLINE = (59, 130, 246, 180)
BUTTON_FILL = (59, 130, 246, 30)
ARROW_COLOR = (234, 179, 8, 220)
CLICK_COLORS = [(220, 38, 38), (34, 197, 94), (234, 179, 8), (168, 85, 247)]
TEXT_COLOR = (255, 255, 255)
PIECE_CIRCLE_R = 24
EMPTY_DOT_R = 4


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
                  "C:/Windows/Fonts/simsun.ttc"]:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    try:
        return ImageFont.truetype("arial.ttf", size)
    except (OSError, IOError):
        pass
    return ImageFont.load_default()


def _compute_grid_cells(board: dict, nw: int, nh: int):
    """从 board 边界计算每个交叉点的像素坐标。返回 9×10 数组 (col,row)。"""
    bl = board.get("left", 0)
    bt = board.get("top", 0)
    br = board.get("right", 1000)
    bb = board.get("bottom", 1000)
    cell_w = (br - bl) / 8
    cell_h = (bb - bt) / 9
    cells = {}
    for col in range(9):
        for row in range(10):
            x = int((bl + col * cell_w) * nw / 1000)
            y = int((bt + (9 - row) * cell_h) * nh / 1000)  # row 0=top, row 9=bottom
            cells[(col + 1, row + 1)] = (x, y)  # 1-indexed: col 1-9, row 1-10
    return cells, cell_w, cell_h


def annotate_board_state(screenshot_bytes: bytes, state: dict) -> bytes:
    """棋盘标注：网格 + 红黑棋子 + 空位圆点 + 按钮。"""
    img = Image.open(BytesIO(screenshot_bytes)).convert("RGBA")
    nw, nh = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _load_font(20)
    small_font = _load_font(12)

    board = state.get("board", {})
    pieces = state.get("pieces", [])
    # 建立已占位集合
    occupied = set()
    for p in pieces:
        bp = p.get("board_pos", {})
        occupied.add((bp.get("col", 0), bp.get("row", 0)))

    # ── Board grid ────────────────────────────────────────────────
    if board:
        bl = int(board.get("left", 0) * nw / 1000)
        bt = int(board.get("top", 0) * nh / 1000)
        br = int(board.get("right", 1000) * nw / 1000)
        bb = int(board.get("bottom", 1000) * nh / 1000)
        cell_w = (br - bl) / 8
        cell_h = (bb - bt) / 9

        # 竖线
        for i in range(9):
            x = int(bl + i * cell_w)
            draw.line([(x, bt), (x, bb)], fill=GRID_COLOR, width=2)
        # 横线
        for i in range(10):
            y = int(bt + i * cell_h)
            draw.line([(bl, y), (br, y)], fill=GRID_COLOR, width=2)

        # 楚河汉界
        river_y = int(bt + 4.5 * cell_h)
        big_font = _load_font(26)
        draw.text((bl + 8, river_y - 16), "楚  河", fill=RIVER_COLOR, font=big_font)
        draw.text((br - 90, river_y - 16), "汉  界", fill=RIVER_COLOR, font=big_font)

        # 空位圆点（所有交叉点，除已被占的）
        for col in range(9):
            for row in range(10):
                if (col + 1, row + 1) in occupied:
                    continue
                cx = int(bl + col * cell_w)
                cy = int(bt + (9 - row) * cell_h)
                r = EMPTY_DOT_R
                draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=EMPTY_DOT)

    # ── Pieces ────────────────────────────────────────────────────
    for p in pieces:
        pp = p.get("pixel_pos", {})
        x = int(pp.get("x", 0) * nw / 1000)
        y = int(pp.get("y", 0) * nh / 1000)
        if x <= 0 and y <= 0:
            continue

        side = p.get("side", "red")
        if side == "red":
            outline, fill_color, text_color = RED_PIECE_OUTLINE, RED_PIECE_FILL, RED_TEXT
        else:
            outline, fill_color, text_color = BLACK_PIECE_OUTLINE, BLACK_PIECE_FILL, BLACK_TEXT

        r = PIECE_CIRCLE_R
        draw.ellipse((x - r, y - r, x + r, y + r), fill=fill_color, outline=outline, width=3)

        # 棋子名
        name = p.get("piece", "?")
        piece_font = _load_font(18)
        _draw_centered(draw, name, x, y, piece_font, text_color)

        # 盘坐标小字
        bp = p.get("board_pos", {})
        label = f"({bp.get('col','?')},{bp.get('row','?')})"
        _draw_centered(draw, label, x, y + r + 12, small_font, (180, 180, 180))

    # ── Buttons ───────────────────────────────────────────────────
    for btn in state.get("buttons", []):
        bx = int(btn.get("x", 0) * nw / 1000)
        by = int(btn.get("y", 0) * nh / 1000)
        if bx <= 0 or by <= 0:
            continue
        bw, bh = 60, 20
        draw.rectangle((bx - bw, by - bh, bx + bw, by + bh),
                       fill=BUTTON_FILL, outline=BUTTON_OUTLINE, width=2)
        _draw_centered(draw, btn.get("text", "?"), bx, by, small_font, BUTTON_OUTLINE[:3])

    img = Image.alpha_composite(img, overlay).convert("RGB")

    # ── Info bar ──────────────────────────────────────────────────
    draw = ImageDraw.Draw(img)
    big_font = _load_font(26)
    st = state.get("screen_type", "?")
    n_p = len(pieces)
    red_n = sum(1 for p in pieces if p.get("side") == "red")
    black_n = n_p - red_n
    draw.rectangle((0, 0, img.width, 48), fill=(0, 0, 0, 210))
    draw.text((10, 6), f"Screen: {st}  |  Red: {red_n}  Black: {black_n}",
              fill=TEXT_COLOR, font=big_font)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def annotate_move(screenshot_bytes: bytes, state: dict, taps: list[tuple]) -> bytes:
    """走法箭头 + 记谱文字。"""
    img = Image.open(BytesIO(screenshot_bytes)).convert("RGBA")
    nw, nh = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _load_font(30)
    small_font = _load_font(16)

    if len(taps) >= 2:
        x1, y1, _ = taps[0]
        x2, y2, desc2 = taps[1]
        fx = int(x1 * nw / 1000) if x1 else 0
        fy = int(y1 * nh / 1000) if y1 else 0
        tx = int(x2 * nw / 1000) if x2 else 0
        ty = int(y2 * nh / 1000) if y2 else 0

        r = 24
        # 起点（绿）
        draw.ellipse((fx - r, fy - r, fx + r, fy + r),
                     fill=(34, 197, 94, 60), outline=(34, 197, 94, 240), width=4)
        # 终点（红）
        draw.ellipse((tx - r, ty - r, tx + r, ty + r),
                     fill=(220, 38, 38, 60), outline=(220, 38, 38, 240), width=4)
        # 箭头
        draw.line([(fx, fy), (tx, ty)], fill=ARROW_COLOR, width=5)
        _draw_arrowhead(draw, fx, fy, tx, ty, size=18, color=ARROW_COLOR[:3])

        draw.text((fx + r + 4, fy - r - 8), "FROM", fill=(34, 197, 94), font=small_font)
        draw.text((tx + r + 4, ty - r - 8), "TO", fill=(220, 38, 38), font=small_font)

        # 记谱（从 desc2 提取）
        import re
        m = re.search(r"(.+?) →", desc2)
        notation = m.group(1) if m else ""
        if notation:
            mid_x, mid_y = (fx + tx) // 2, (fy + ty) // 2 - 24
            _draw_centered(draw, notation, mid_x, mid_y, font, ARROW_COLOR[:3])

    img = Image.alpha_composite(img, overlay).convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def annotate_clicks(screenshot_bytes: bytes, actions: list) -> bytes:
    """点击序列可视化。"""
    img = Image.open(BytesIO(screenshot_bytes)).convert("RGBA")
    nw, nh = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _load_font(22)
    desc_font = _load_font(16)

    tap_idx = 0
    for action in actions:
        if action.type != "tap" or action.x1 is None or action.y1 is None:
            continue
        x = int(action.x1 * nw / 1000)
        y = int(action.y1 * nh / 1000)
        color = CLICK_COLORS[tap_idx % len(CLICK_COLORS)]
        r = 30
        tap_idx += 1

        draw.ellipse((x - r, y - r, x + r, y + r), fill=(*color, 200), outline=color, width=3)
        num = str(tap_idx)
        bbox = draw.textbbox((0, 0), num, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((x - tw // 2, y - th // 2), num, fill=TEXT_COLOR, font=font)

        desc = (action.description or "")[:50]
        if desc:
            _draw_centered(draw, desc, x, y + r + 18, desc_font, TEXT_COLOR)

    img = Image.alpha_composite(img, overlay).convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _draw_centered(draw, text, x, y, font, color):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    cx, cy = x - tw // 2, y - th // 2
    draw.text((cx + 1, cy + 1), text, fill=(0, 0, 0), font=font)
    draw.text((cx, cy), text, fill=color, font=font)


def _draw_arrowhead(draw, x1, y1, x2, y2, size=14, color=ARROW_COLOR):
    import math
    angle = math.atan2(y2 - y1, x2 - x1)
    spread = math.pi / 6
    p1 = (x2 - int(size * math.cos(angle - spread)),
          y2 - int(size * math.sin(angle - spread)))
    p2 = (x2 - int(size * math.cos(angle + spread)),
          y2 - int(size * math.sin(angle + spread)))
    draw.polygon([(x2, y2), p1, p2], fill=(*color, 220))

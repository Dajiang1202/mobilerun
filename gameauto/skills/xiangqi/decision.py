"""天天象棋决策引擎 — VLM 识别 + 象棋引擎搜索 = 最优走法。

流程:
  1. 从 VLM 结果构建 Board
  2. 调用 engine.find_best_move() 搜索
  3. 映射到 pixel_pos 生成点击 Action
  4. 记谱（如"炮二平五"）
"""

from __future__ import annotations

import logging

from gameauto.core.orchestration.base import Action
from gameauto.skills.xiangqi.engine import Board, find_best_move_pikafish

logger = logging.getLogger("gameauto.xiangqi.decision")

# 操作间延迟: 走子后等对手(~1s); 框架在动作步之间还会额外等动画。
_MOVE_DELAY = Action(type="wait", duration_ms=1000, description="Wait for opponent move")


def decide(state: dict, round_num: int) -> tuple[list[Action], dict | None]:
    """统一决策入口。

    Args:
        state: 感知返回的解析后状态 dict
        round_num: 当前回合数

    Returns:
        (actions, move): actions 为 Action 列表; move 为本回合走法
        {"from":{col,row},"to":{col,row}} 或 None(非对局走子)。
    """
    screen_type = state.get("screen_type", "unknown")
    pieces = state.get("pieces", [])
    buttons = state.get("buttons", [])
    button_texts = {b.get("text", "") for b in buttons}

    logger.info("Decision: screen=%s, pieces=%d, buttons=%s",
                 screen_type, len(pieces), button_texts)

    # ── 菜单界面 ──────────────────────────────────────────────────
    if screen_type == "menu":
        if "开始游戏" in button_texts or "开始" in button_texts:
            btn = _find_button(buttons, "开始游戏") or _find_button(buttons, "开始")
            logger.info("Menu: clicking '%s'", btn["text"])
            return [_make_tap(btn, "Click '开始游戏'"),
                    Action(type="wait", duration_ms=10000, description="Wait for match")], None
        logger.info("Menu: no start button found")
        return [], None

    # ── 游戏结束 ──────────────────────────────────────────────────
    # 注: 单局模式由 states.py 检测 game_over 后直接停止, 不走这里。
    if screen_type == "game_over":
        return [], None

    # ── 对局中 ────────────────────────────────────────────────────
    if screen_type != "playing" or not pieces:
        return [], None

    # 判断红黑方（根据棋子数量或位置推断：己方在下方，即 row 较小的为红方）
    red_pieces = [p for p in pieces if p.get("side") == "red"]
    if not red_pieces:
        logger.info("No red pieces found — likely opponent's turn or recognition issue")
        return [], None

    side = "red"  # 默认红方（玩家在下方）

    # ── 构建棋盘 + 搜索最优走法 ───────────────────────────────────
    try:
        board = Board.from_pieces(pieces, side_to_move=side)
    except Exception:
        logger.exception("Failed to build board from pieces")
        return [], None

    best = find_best_move_pikafish(board, side=side, movetime=2000)
    if best is None:
        logger.warning("No legal move found")
        return [], None

    notation = best["notation"]
    from_pos = best["from"]   # {"col": 2, "row": 8}
    to_pos = best["to"]       # {"col": 5, "row": 5}

    logger.info("Best move: %s (from %s to %s)", notation, from_pos, to_pos)

    # ── 查找 pixel_pos（优先从 board 边界计算精确位置）─────────
    board_rect = state.get("board", {})
    from_pixel = _pixel_from_board(board_rect, from_pos["col"], from_pos["row"])
    to_pixel = _pixel_from_board(board_rect, to_pos["col"], to_pos["row"])

    # Fallback: 从 pixel_pos 查找
    if from_pixel is None:
        from_pixel = _find_pixel(pieces, from_pos["col"], from_pos["row"])
    if to_pixel is None:
        to_pixel = _find_pixel(pieces, to_pos["col"], to_pos["row"])

    if from_pixel is None or to_pixel is None:
        logger.error("Cannot find pixel_pos for move: from=%s to=%s", from_pos, to_pos)
        return [], None

    logger.info("Tap: (%d,%d) → (%d,%d)", from_pixel[0], from_pixel[1],
                 to_pixel[0], to_pixel[1])

    actions = [
        _make_tap_pixel(from_pixel[0], from_pixel[1], f"Select piece at {from_pos['col']},{from_pos['row']}"),
        _make_tap_pixel(to_pixel[0], to_pixel[1], f"{notation} → target {to_pos['col']},{to_pos['row']}"),
        _MOVE_DELAY,
    ]
    move = {"from": from_pos, "to": to_pos}
    return actions, move


def _pixel_from_board(board: dict, col: int, row: int) -> tuple[int, int] | None:
    """从棋盘边界 + 逻辑坐标计算精确像素坐标。

    col 1-9, row 1-10 (1=红底线,10=黑底线)
    board: {"left": 50, "top": 80, "right": 950, "bottom": 920}
    """
    if not board:
        return None
    bl = board.get("left")
    bt = board.get("top")
    br = board.get("right")
    bb = board.get("bottom")
    if not all(v is not None for v in (bl, bt, br, bb)):
        return None

    cell_w = (br - bl) / 8
    cell_h = (bb - bt) / 9
    # col 1-9 → x: left + (col-1) * cell_w
    x = int(bl + (col - 1) * cell_w)
    # row 1(bottom/red) → 10(top/black): y = bottom - (row-1) * cell_h
    y = int(bb - (row - 1) * cell_h)
    return (x, y)


def _find_pixel(pieces: list[dict], col: int, row: int) -> tuple[int, int] | None:
    """从 pieces 列表中按 board_pos 查找 pixel_pos。"""
    for p in pieces:
        bp = p.get("board_pos", {})
        if bp.get("col") == col and bp.get("row") == row:
            pp = p.get("pixel_pos", {})
            x = pp.get("x")
            y = pp.get("y")
            if x is not None and y is not None:
                return (int(x), int(y))
    return None


def _find_button(buttons: list[dict], text: str) -> dict | None:
    for b in buttons:
        if text in b.get("text", ""):
            return b
    return None


def _make_tap(btn: dict, description: str) -> Action:
    return Action(
        type="tap", x1=btn.get("x", 500), y1=btn.get("y", 500),
        duration_ms=150, description=description,
    )


def _make_tap_pixel(x: int, y: int, description: str) -> Action:
    return Action(
        type="tap", x1=x, y1=y, duration_ms=150, description=description,
    )

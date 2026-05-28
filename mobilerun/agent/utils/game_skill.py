"""Greedy match-3 solver — finds valid swaps from a structured board JSON.

Pure Python, no VLM — runs in microseconds. The VLM only needs to produce the
board JSON (perception), then this module finds the swap and computes coordinates.
"""

from __future__ import annotations

import json
from typing import Any


def solve_board(board_json: dict | str) -> dict:
    """Find a valid 3-match swap on the given board.

    Args:
        board_json: Dict (or JSON string) with keys:
            ``rows``, ``cols``, ``tiles`` (2D list of strings),
            ``board_left``, ``board_top``, ``board_right``, ``board_bottom``
            (all in [0-1000] normalized coordinates).

    Returns:
        Dict with ``found``, ``from``, ``to``, ``tile``, ``direction``,
        ``coordinates``, and ``match_description``.
    """
    if isinstance(board_json, str):
        board_json = json.loads(board_json)

    # 直接从 tiles 二维数组推导行列数，不依赖 JSON 中的 rows/cols 字段（可能不准确）
    tiles: list[list[str]] = [[str(t).lower().strip() for t in row] for row in board_json["tiles"]]
    rows = len(tiles)
    cols = len(tiles[0]) if tiles else 0
    board_left: float = float(board_json.get("board_left", 0))
    board_top: float = float(board_json.get("board_top", 0))
    board_right: float = float(board_json.get("board_right", 1000))
    board_bottom: float = float(board_json.get("board_bottom", 1000))

    # 每个格子的归一化尺寸 = 边界范围 / 行列数
    cell_w = (board_right - board_left) / cols
    cell_h = (board_bottom - board_top) / rows

    # ── 贪心扫描：从上到下、从左到右，遇到第一个有效交换立即返回 ──────
    for r in range(rows):
        for c in range(cols):
            tile_a = _tile(tiles, r, c)
            if tile_a is None:
                continue

            # Check right neighbor
            if c + 1 < cols:
                tile_b = _tile(tiles, r, c + 1)
                if tile_b is not None and tile_a != tile_b:
                    if _would_match(tiles, r, c, r, c + 1, rows, cols):
                        return _result(
                            True, r, c, r, c + 1, tile_a, tile_b,
                            "horizontal", board_left, board_top, cell_w, cell_h,
                        )

            # Check bottom neighbor
            if r + 1 < rows:
                tile_b = _tile(tiles, r + 1, c)
                if tile_b is not None and tile_a != tile_b:
                    if _would_match(tiles, r, c, r + 1, c, rows, cols):
                        return _result(
                            True, r, c, r + 1, c, tile_a, tile_b,
                            "vertical", board_left, board_top, cell_w, cell_h,
                        )

    return {"found": False, "reason": "No valid 3-match swap found on the current board"}


def _tile(tiles: list[list[str]], r: int, c: int) -> str | None:
    if 0 <= r < len(tiles) and 0 <= c < len(tiles[r]):
        val = tiles[r][c].strip()
        return val if val else None
    return None


def _would_match(
    tiles: list[list[str]],
    r1: int, c1: int,
    r2: int, c2: int,
    rows: int, cols: int,
) -> bool:
    """检查交换 (r1,c1) 和 (r2,c2) 后是否产生至少一个 3-连消除。"""
    # 浅拷贝棋盘模拟交换，避免修改原始数据
    swapped = [list(row) for row in tiles]
    swapped[r1][c1], swapped[r2][c2] = swapped[r2][c2], swapped[r1][c1]

    for r, c in ((r1, c1), (r2, c2)):
        if _count_run(swapped, r, c, 0, 1, rows, cols) >= 3:
            return True
        if _count_run(swapped, r, c, 1, 0, rows, cols) >= 3:
            return True
    return False


def _count_run(
    tiles: list[list[str]],
    r: int, c: int,
    dr: int, dc: int,
    rows: int, cols: int,
) -> int:
    """Count consecutive same-type tiles in a line through (r,c)."""
    tile = tiles[r][c]
    if not tile:
        return 0
    count = 1
    # positive direction
    nr, nc = r + dr, c + dc
    while 0 <= nr < rows and 0 <= nc < cols and tiles[nr][nc] == tile:
        count += 1
        nr += dr
        nc += dc
    # negative direction
    nr, nc = r - dr, c - dc
    while 0 <= nr < rows and 0 <= nc < cols and tiles[nr][nc] == tile:
        count += 1
        nr -= dr
        nc -= dc
    return count


def _result(
    found: bool,
    r1: int, c1: int,
    r2: int, c2: int,
    tile_a: str, tile_b: str,
    direction: str,
    board_left: float, board_top: float,
    cell_w: float, cell_h: float,
) -> dict[str, Any]:
    if not found:
        return {"found": False}

    def center(row: int, col: int) -> list[int]:
        # 计算格子中心点：边界偏移 + (col+0.5)*格宽 (归一化坐标)
        x = round(board_left + (col + 0.5) * cell_w)
        y = round(board_top + (row + 0.5) * cell_h)
        return [x, y]

    from_cell = f"({r1},{c1})"
    to_cell = f"({r2},{c2})"
    if direction == "horizontal":
        match_desc = f"Swap {tile_a} at {from_cell} with {tile_b} at {to_cell} — horizontal 3-match"
    else:
        match_desc = f"Swap {tile_a} at {from_cell} with {tile_b} at {to_cell} — vertical 3-match"

    return {
        "found": True,
        "from": [r1, c1],
        "to": [r2, c2],
        "tile_a": tile_a,
        "tile_b": tile_b,
        "direction": direction,
        "match_description": match_desc,
        "coordinates": {
            "from": center(r1, c1),
            "to": center(r2, c2),
        },
    }

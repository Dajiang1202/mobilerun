"""Greedy match-3 solver — finds valid swaps from a structured board JSON.

Pure Python, no VLM — runs in microseconds. The VLM only needs to produce the
board JSON (perception), then this module finds the swap and computes coordinates.
"""

from __future__ import annotations

import json
import random
from typing import Any

# 不可交换/不可消除的特殊块类型
_NON_SWAPPABLE = {"empty", "blocked"}

# 疑似误识别的方块关键词（blocked/empty 被 VLM 误识别为普通方块时，常被标为方形）
_DEPRIORITIZE_KEYWORDS = ("square", "block", "cube", "box")


def _deprioritize(tile_name: str) -> bool:
    """Check if a tile name suggests a misrecognized blocked/empty cell."""
    return any(kw in tile_name for kw in _DEPRIORITIZE_KEYWORDS)


def _sort_key(cand: tuple) -> int:
    _r1, _c1, _r2, _c2, tile_a, tile_b, _dir = cand
    return 1 if (_deprioritize(tile_a) or _deprioritize(tile_b)) else 0


def _parse_board(board_json: dict | str) -> tuple[list[list[str]], int, int, float, float, float, float]:
    """Parse board JSON into normalized tiles and geometry.

    Returns:
        (tiles, rows, cols, board_left, board_top, board_right, board_bottom)
    """
    if isinstance(board_json, str):
        board_json = json.loads(board_json)

    tiles: list[list[str]] = [[str(t).lower().strip() for t in row] for row in board_json["tiles"]]
    rows = len(tiles)
    cols = max((len(row) for row in tiles), default=0)
    for row in tiles:
        if len(row) < cols:
            row.extend(["empty"] * (cols - len(row)))
    board_left = float(board_json.get("board_left", 0))
    board_top = float(board_json.get("board_top", 0))
    board_right = float(board_json.get("board_right", 1000))
    board_bottom = float(board_json.get("board_bottom", 1000))
    return tiles, rows, cols, board_left, board_top, board_right, board_bottom


def _collect_candidates(
    tiles: list[list[str]], rows: int, cols: int,
) -> list[tuple[int, int, int, int, str, str, str]]:
    """Collect all valid 3-match swap candidates from the board.

    Returns:
        Sorted list of (r1, c1, r2, c2, tile_a, tile_b, direction).
        Sorted so non-deprioritized (normal) tiles come first.
    """
    candidates: list[tuple[int, int, int, int, str, str, str]] = []

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
                        candidates.append((r, c, r, c + 1, tile_a, tile_b, "horizontal"))

            # Check bottom neighbor
            if r + 1 < rows:
                tile_b = _tile(tiles, r + 1, c)
                if tile_b is not None and tile_a != tile_b:
                    if _would_match(tiles, r, c, r + 1, c, rows, cols):
                        candidates.append((r, c, r + 1, c, tile_a, tile_b, "vertical"))

    # ── 方形块降权：疑似误识别的方块排在最后 ──────────────────────────
    candidates.sort(key=_sort_key)
    return candidates


def _are_swaps_independent(
    swap_a: tuple[int, int, int, int, str, str, str],
    swap_b: tuple[int, int, int, int, str, str, str],
) -> bool:
    """Two swaps are independent if they share no grid cell positions."""
    cells_a = {(swap_a[0], swap_a[1]), (swap_a[2], swap_a[3])}
    cells_b = {(swap_b[0], swap_b[1]), (swap_b[2], swap_b[3])}
    return cells_a.isdisjoint(cells_b)


def _select_independent_swaps(
    candidates: list[tuple[int, int, int, int, str, str, str]],
    max_steps: int,
) -> list[tuple[int, int, int, int, str, str, str]]:
    """Select up to max_steps mutually-independent swaps from priority-sorted candidates.

    When max_steps=1, randomly picks from the best-priority tier (preserves
    existing random-debounce behavior).

    When max_steps>1, uses greedy selection: takes the first candidate, then
    scans forward for the next independent candidate, repeating until max_steps
    is reached or candidates are exhausted.
    """
    if max_steps == 1:
        # Preserve existing random-debounce: pick randomly from best-priority tier
        best_priority = _sort_key(candidates[0])
        top = [c for c in candidates if _sort_key(c) == best_priority]
        return [random.choice(top)]

    # Greedy: pick first, then skip overlapping, pick next independent
    selected: list[tuple[int, int, int, int, str, str, str]] = []
    used_cells: set[tuple[int, int]] = set()

    for cand in candidates:
        if len(selected) >= max_steps:
            break
        cells = {(cand[0], cand[1]), (cand[2], cand[3])}
        if cells.isdisjoint(used_cells):
            selected.append(cand)
            used_cells.update(cells)

    return selected


def solve_board(board_json: dict | str) -> dict:
    """Find a valid 3-match swap on the given board.

    Collects ALL valid swaps, deprioritizes square-shaped tiles (often
    misrecognized blocked/empty cells), then randomly picks from the best
    candidates to avoid deterministic loops when recognition is unstable.

    Args:
        board_json: Dict (or JSON string) with keys:
            ``rows``, ``cols``, ``tiles`` (2D list of strings),
            ``board_left``, ``board_top``, ``board_right``, ``board_bottom``
            (all in [0-1000] normalized coordinates).

    Returns:
        Dict with ``found``, ``from``, ``to``, ``tile_a``, ``tile_b``,
        ``direction``, ``coordinates``, and ``match_description``.
    """
    tiles, rows, cols, board_left, board_top, board_right, board_bottom = _parse_board(board_json)

    cell_w = (board_right - board_left) / cols
    cell_h = (board_bottom - board_top) / rows

    candidates = _collect_candidates(tiles, rows, cols)
    if not candidates:
        return {"found": False, "reason": "No valid 3-match swap found on the current board"}

    # ── 从最高优先级候选中随机选择一个 ──────────────────────────────
    best_priority = _sort_key(candidates[0])
    top_candidates = [c for c in candidates if _sort_key(c) == best_priority]
    chosen = random.choice(top_candidates)
    r1, c1, r2, c2, tile_a, tile_b, direction = chosen

    return _result(
        True, r1, c1, r2, c2, tile_a, tile_b,
        direction, board_left, board_top, cell_w, cell_h,
    )


def solve_board_multi(board_json: dict | str, max_steps: int = 1) -> list[dict]:
    """Find up to max_steps independent valid 3-match swaps on the board.

    Two swaps are independent if they share no grid cell positions.

    Args:
        board_json: Same format as solve_board().
        max_steps: Maximum number of independent swaps to return (default 1).

    Returns:
        List of result dicts in the same format as solve_board()'s return.
        Empty list means no valid swaps found.
    """
    tiles, rows, cols, board_left, board_top, board_right, board_bottom = _parse_board(board_json)

    cell_w = (board_right - board_left) / cols
    cell_h = (board_bottom - board_top) / rows

    candidates = _collect_candidates(tiles, rows, cols)
    if not candidates:
        return []

    selected = _select_independent_swaps(candidates, max_steps)

    results: list[dict] = []
    for cand in selected:
        r1, c1, r2, c2, tile_a, tile_b, direction = cand
        results.append(_result(
            True, r1, c1, r2, c2, tile_a, tile_b,
            direction, board_left, board_top, cell_w, cell_h,
        ))
    return results


def _tile(tiles: list[list[str]], r: int, c: int) -> str | None:
    if 0 <= r < len(tiles) and 0 <= c < len(tiles[r]):
        val = tiles[r][c].strip()
        if not val or val in _NON_SWAPPABLE:
            return None
        return val
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
    if not tile or tile in _NON_SWAPPABLE:
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

"""Greedy match-3 solver — finds valid swaps from board JSON.

Pure Python, no VLM — runs in microseconds. Adapted from mobilerun.

优先级（从高到低）:
  1. 邻近"特殊砖块"(blocked / 障碍型)的消除 —— 优先打掉障碍。
  2. 普通可用消除。
  3. 棋子本身是 square/block/cube/box 形状的交换 —— 降权。

多步选择（max_steps >= 2）:
  - 第一步取最高优先级。
  - 后续每一步在剩余不冲突的候选里，选离已选交换最远的那一个，
    使第一步消除产生的连锁尽量不影响第二步。
"""

from __future__ import annotations

import json
import math
import random
from typing import Any

_NON_SWAPPABLE = {"empty", "blocked"}
# 形状像障碍的棋子（本身交换通常没意义）。
_DEPRIORITIZE_KEYWORDS = ("square", "block", "cube", "box")


def _deprioritize(tile_name: str) -> bool:
    return any(kw in tile_name for kw in _DEPRIORITIZE_KEYWORDS)


def _is_blocker(tile_name: str) -> bool:
    """是否为"特殊砖块"——在它旁边消除有价值（破冰/破笼/砸箱子等）。

    覆盖字面量 'blocked' 以及障碍形状关键词。
    """
    return tile_name == "blocked" or _deprioritize(tile_name)


def _parse_board(board_json: dict | str) -> tuple[list[list[str]], int, int, float, float, float, float]:
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


def _tile(tiles: list[list[str]], r: int, c: int) -> str | None:
    if 0 <= r < len(tiles) and 0 <= c < len(tiles[r]):
        val = tiles[r][c].strip()
        if not val or val in _NON_SWAPPABLE:
            return None
        return val
    return None


def _would_match(tiles, r1, c1, r2, c2, rows, cols) -> bool:
    swapped = [list(row) for row in tiles]
    swapped[r1][c1], swapped[r2][c2] = swapped[r2][c2], swapped[r1][c1]
    for r, c in ((r1, c1), (r2, c2)):
        if _count_run(swapped, r, c, 0, 1, rows, cols) >= 3:
            return True
        if _count_run(swapped, r, c, 1, 0, rows, cols) >= 3:
            return True
    return False


def _count_run(tiles, r, c, dr, dc, rows, cols) -> int:
    tile = tiles[r][c]
    if not tile or tile in _NON_SWAPPABLE:
        return 0
    count = 1
    nr, nc = r + dr, c + dc
    while 0 <= nr < rows and 0 <= nc < cols and tiles[nr][nc] == tile:
        count += 1
        nr += dr
        nc += dc
    nr, nc = r - dr, c - dc
    while 0 <= nr < rows and 0 <= nc < cols and tiles[nr][nc] == tile:
        count += 1
        nr -= dr
        nc -= dc
    return count


def _near_blocker(tiles, r, c, rows, cols) -> bool:
    """(r,c) 的四邻域内是否有特殊砖块。"""
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        nr, nc = r + dr, c + dc
        if 0 <= nr < rows and 0 <= nc < cols:
            if _is_blocker(tiles[nr][nc]):
                return True
    return False


def _collect_candidates(tiles, rows, cols) -> list[tuple]:
    """收集所有可形成 3 消的有效交换。

    每条候选: (r1, c1, r2, c2, tile_a, tile_b, direction, near_block)
    near_block 表示交换两端任一格紧邻特殊砖块（优先消除）。
    """
    candidates: list[tuple] = []
    for r in range(rows):
        for c in range(cols):
            tile_a = _tile(tiles, r, c)
            if tile_a is None:
                continue
            if c + 1 < cols:
                tile_b = _tile(tiles, r, c + 1)
                if tile_b is not None and tile_a != tile_b:
                    if _would_match(tiles, r, c, r, c + 1, rows, cols):
                        near = (_near_blocker(tiles, r, c, rows, cols)
                                or _near_blocker(tiles, r, c + 1, rows, cols))
                        candidates.append((r, c, r, c + 1, tile_a, tile_b, "horizontal", near))
            if r + 1 < rows:
                tile_b = _tile(tiles, r + 1, c)
                if tile_b is not None and tile_a != tile_b:
                    if _would_match(tiles, r, c, r + 1, c, rows, cols):
                        near = (_near_blocker(tiles, r, c, rows, cols)
                                or _near_blocker(tiles, r + 1, c, rows, cols))
                        candidates.append((r, c, r + 1, c, tile_a, tile_b, "vertical", near))
    candidates.sort(key=_sort_key)
    return candidates


def _sort_key(cand: tuple) -> tuple[int, int]:
    """升序排：值小优先级高。

    主键 near_rank: 邻近特殊砖块的交换排最前（优化点 2）。
    次键 deprio_rank: 棋子本身是障碍形状的交换降权。
    """
    _r1, _c1, _r2, _c2, tile_a, tile_b, _dir, near_block = cand
    near_rank = 0 if near_block else 1
    deprio_rank = 1 if (_deprioritize(tile_a) or _deprioritize(tile_b)) else 0
    return (near_rank, deprio_rank)


def _cells(cand: tuple) -> set[tuple[int, int]]:
    return {(cand[0], cand[1]), (cand[2], cand[3])}


def _center(cand: tuple) -> tuple[float, float]:
    """交换的几何中心（格坐标）。"""
    return ((cand[0] + cand[2]) / 2.0, (cand[1] + cand[3]) / 2.0)


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _priority_rank(sort_key: tuple[int, int]) -> int:
    """把 (near_rank, deprio_rank) 压成单一可比较的优先级数，越小越优。"""
    near, deprio = sort_key
    return near * 10 + deprio


def _select_independent_swaps(candidates, max_steps) -> list[tuple]:
    if max_steps == 1:
        best_priority = _sort_key(candidates[0])
        top = [c for c in candidates if _sort_key(c) == best_priority]
        return [random.choice(top)]

    # max_steps >= 2（优化点 3）:
    #   第一步 = 最高优先级（保留优化点 2 的破障倾向）；
    #   之后每一步在剩余不冲突的候选里挑"离已选最远"的，最大化两步间距，
    #   使第一步的连锁/塌落尽量不波及第二步。距离相同时优先取高优先级。
    selected: list[tuple] = []
    used_cells: set[tuple[int, int]] = set()

    best_priority = _sort_key(candidates[0])
    top = [c for c in candidates if _sort_key(c) == best_priority]
    first = random.choice(top)
    selected.append(first)
    used_cells |= _cells(first)

    while len(selected) < max_steps:
        remaining = [c for c in candidates if _cells(c).isdisjoint(used_cells)]
        if not remaining:
            break
        sel_centers = [_center(c) for c in selected]
        best_cand = None
        best_score: tuple[float, int] | None = None
        for c in remaining:
            cc = _center(c)
            min_d = min(_dist(cc, sc) for sc in sel_centers)
            # 距离越大越好；距离并列时，优先级数越小（越优）越好 → 取负
            score = (min_d, -_priority_rank(_sort_key(c)))
            if best_score is None or score > best_score:
                best_score = score
                best_cand = c
        selected.append(best_cand)
        used_cells |= _cells(best_cand)
    return selected


def _result(found, r1, c1, r2, c2, tile_a, tile_b, direction,
            board_left, board_top, cell_w, cell_h) -> dict[str, Any]:
    if not found:
        return {"found": False}

    def center(row, col):
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
        "coordinates": {"from": center(r1, c1), "to": center(r2, c2)},
    }


# ── Public API ──────────────────────────────────────────────────────────


def solve_board(board_json: dict | str) -> dict:
    """Find a valid 3-match swap on the board."""
    tiles, rows, cols, bl, bt, br, bb = _parse_board(board_json)
    cell_w = (br - bl) / cols
    cell_h = (bb - bt) / rows
    candidates = _collect_candidates(tiles, rows, cols)
    if not candidates:
        return {"found": False, "reason": "No valid 3-match swap found"}
    best_priority = _sort_key(candidates[0])
    top = [c for c in candidates if _sort_key(c) == best_priority]
    chosen = random.choice(top)
    r1, c1, r2, c2, tile_a, tile_b, direction, _near = chosen
    return _result(True, r1, c1, r2, c2, tile_a, tile_b, direction, bl, bt, cell_w, cell_h)


def solve_board_multi(board_json: dict | str, max_steps: int = 1) -> list[dict]:
    """Find up to max_steps independent valid 3-match swaps."""
    tiles, rows, cols, bl, bt, br, bb = _parse_board(board_json)
    cell_w = (br - bl) / cols
    cell_h = (bb - bt) / rows
    candidates = _collect_candidates(tiles, rows, cols)
    if not candidates:
        return []
    selected = _select_independent_swaps(candidates, max_steps)
    results = []
    for cand in selected:
        r1, c1, r2, c2, tile_a, tile_b, direction, _near = cand
        results.append(_result(True, r1, c1, r2, c2, tile_a, tile_b, direction, bl, bt, cell_w, cell_h))
    return results

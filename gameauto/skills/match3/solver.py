"""Greedy match-3 solver — finds valid swaps from board JSON.

Pure Python, no VLM — runs in microseconds. Adapted from mobilerun.
"""

from __future__ import annotations

import json
import random
from typing import Any

_NON_SWAPPABLE = {"empty", "blocked"}
_DEPRIORITIZE_KEYWORDS = ("square", "block", "cube", "box")


def _deprioritize(tile_name: str) -> bool:
    return any(kw in tile_name for kw in _DEPRIORITIZE_KEYWORDS)


def _sort_key(cand: tuple) -> int:
    _r1, _c1, _r2, _c2, tile_a, tile_b, _dir = cand
    return 1 if (_deprioritize(tile_a) or _deprioritize(tile_b)) else 0


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


def _collect_candidates(tiles, rows, cols) -> list[tuple]:
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
                        candidates.append((r, c, r, c + 1, tile_a, tile_b, "horizontal"))
            if r + 1 < rows:
                tile_b = _tile(tiles, r + 1, c)
                if tile_b is not None and tile_a != tile_b:
                    if _would_match(tiles, r, c, r + 1, c, rows, cols):
                        candidates.append((r, c, r + 1, c, tile_a, tile_b, "vertical"))
    candidates.sort(key=_sort_key)
    return candidates


def _select_independent_swaps(candidates, max_steps) -> list[tuple]:
    if max_steps == 1:
        best_priority = _sort_key(candidates[0])
        top = [c for c in candidates if _sort_key(c) == best_priority]
        return [random.choice(top)]
    selected: list[tuple] = []
    used_cells: set[tuple[int, int]] = set()
    for cand in candidates:
        if len(selected) >= max_steps:
            break
        cells = {(cand[0], cand[1]), (cand[2], cand[3])}
        if cells.isdisjoint(used_cells):
            selected.append(cand)
            used_cells.update(cells)
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
    r1, c1, r2, c2, tile_a, tile_b, direction = chosen
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
        r1, c1, r2, c2, tile_a, tile_b, direction = cand
        results.append(_result(True, r1, c1, r2, c2, tile_a, tile_b, direction, bl, bt, cell_w, cell_h))
    return results

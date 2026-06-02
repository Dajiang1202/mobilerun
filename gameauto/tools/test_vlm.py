#!/usr/bin/env python3
"""VLM 感知 + 决策可视化测试 — 改下面的配置，直接运行。

输出:
    <OUT_DIR>/perception.json    VLM 原始返回 + 解析结果
    <OUT_DIR>/perception.png     感知可视化
    <OUT_DIR>/decision.json      决策结果
    <OUT_DIR>/clicks.png         点击序列可视化
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from gameauto.config.loader import load_global_config
from gameauto.core.perception.vlm_client import VlmClient


# ═══════════════════════════════════════════════════════════════════════
# 配置区 — 改这里
# ═══════════════════════════════════════════════════════════════════════

# 截图路径
IMAGE_PATH = r"D:\gameauto\mobilerun\gameauto\logs\20260602_225251_351\round_011\screenshot.png"

# 游戏类型: "doudizhu" | "match3"
SKILL = "doudizhu"

# 输出目录 (相对于 gameauto/ 根目录)
OUT_DIR = str(Path(__file__).parent.parent / "test_vlm_output")

# 消消乐专用: 每轮最多几步交换
MATCH3_MAX_STEPS = 2

# ═══════════════════════════════════════════════════════════════════════


async def main():
    image_path = Path(IMAGE_PATH)
    if not image_path.exists():
        print(f"ERROR: Image not found: {IMAGE_PATH}")
        sys.exit(1)

    out_dir = Path(OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshot = image_path.read_bytes()
    print(f"Image: {IMAGE_PATH}  Skill: {SKILL}  Out: {out_dir}")

    # ── VLM ─────────────────────────────────────────────────────────
    global_cfg = load_global_config()
    vlm_cfg = global_cfg.get("vlm", {})
    vlm = VlmClient(
        model=vlm_cfg.get("model", "gpt-4o"),
        base_url=vlm_cfg.get("base_url", ""),
        api_key=vlm_cfg.get("api_key", ""),
    )
    print(f"VLM: {vlm_cfg.get('model')} @ {vlm_cfg.get('base_url')}\n")

    # ── Run ─────────────────────────────────────────────────────────
    if SKILL == "doudizhu":
        await _test_doudizhu(vlm, screenshot, out_dir)
    elif SKILL == "match3":
        await _test_match3(vlm, screenshot, out_dir)
    else:
        print(f"Unknown skill: {SKILL}")


# ═══════════════════════════════════════════════════════════════════════
# 斗地主
# ═══════════════════════════════════════════════════════════════════════

async def _test_doudizhu(vlm: VlmClient, screenshot: bytes, out_dir: Path):
    from gameauto.skills.doudizhu.perception import DouDiZhuPerception
    from gameauto.skills.doudizhu.decision import decide_bidding, decide_playing
    from gameauto.skills.doudizhu.visualizer import annotate_game_state, annotate_clicks

    prompt_path = Path(__file__).parent.parent / "skills" / "doudizhu" / "prompts" / "doudizhu.jinja2"
    perception = DouDiZhuPerception.from_prompt_file(vlm, str(prompt_path))

    print("=== VLM Perception ===")
    result = await perception.recognize(screenshot)
    state = result.parsed

    # 保存 VLM 原始返回
    (out_dir / "perception.json").write_text(json.dumps({
        "raw_response": result.raw_response,
        "parsed": state,
        "latency_ms": result.latency_ms,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    phase = state.get("phase", "?")
    buttons = state.get("buttons", [])
    cards = state.get("hand_cards", [])
    print(f"Phase: {phase}  Buttons: {len(buttons)}  Cards: {len(cards)}")
    for b in buttons:
        print(f"  [{b.get('text','?')}] active={b.get('active','?')}  ({b['x']},{b['y']})")
    print(f"  Cards: {len(cards)} positions")

    # 感知可视化
    if state:
        png = annotate_game_state(screenshot, state)
        (out_dir / "perception.png").write_bytes(png)
        print(f"→ {out_dir / 'perception.png'}")

    # 决策
    print("\n=== Decision ===")
    if phase == "bidding":
        actions = decide_bidding(buttons)
    else:
        actions = decide_playing(buttons, cards)

    (out_dir / "decision.json").write_text(json.dumps({
        "phase": phase,
        "actions": [{"type": a.type, "x": a.x1, "y": a.y1, "desc": a.description} for a in actions],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    for a in actions:
        print(f"  → {a.description} @ ({a.x1:.0f}, {a.y1:.0f})")

    # 点击可视化
    if actions:
        base_img = (out_dir / "perception.png").read_bytes()
        png = annotate_clicks(base_img, actions)
        (out_dir / "clicks.png").write_bytes(png)
        print(f"→ {out_dir / 'clicks.png'}")

    print(f"\nDone: {out_dir}/")


# ═══════════════════════════════════════════════════════════════════════
# 消消乐
# ═══════════════════════════════════════════════════════════════════════

async def _test_match3(vlm: VlmClient, screenshot: bytes, out_dir: Path):
    from gameauto.skills.match3.perception import Match3Perception
    from gameauto.skills.match3.solver import solve_board_multi
    from gameauto.skills.match3.visualizer import annotate_board, annotate_swipe, annotate_multi_swipe
    from gameauto.utils.images import image_dimensions

    prompt_path = Path(__file__).parent.parent / "skills" / "match3" / "prompts" / "generic.jinja2"
    perception = Match3Perception.from_prompt_file(vlm, str(prompt_path))

    print("=== VLM Perception ===")
    result = await perception.recognize(screenshot)
    board = result.parsed

    (out_dir / "perception.json").write_text(json.dumps({
        "raw_response": result.raw_response,
        "parsed": board,
        "latency_ms": result.latency_ms,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = board.get("rows", "?")
    cols = board.get("cols", "?")
    print(f"Board: {rows}x{cols}")
    for i, row in enumerate(board.get("tiles", [])[:5]):
        print(f"  row{i}: {row}")

    # 棋盘可视化
    if board and "tiles" in board:
        png = annotate_board(screenshot, board)
        (out_dir / "perception.png").write_bytes(png)
        print(f"→ {out_dir / 'perception.png'}")

    # 求解
    print("\n=== Solver ===")
    swaps = solve_board_multi(board, max_steps=MATCH3_MAX_STEPS)
    (out_dir / "decision.json").write_text(json.dumps({
        "swaps_found": len(swaps),
        "swaps": swaps,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Found {len(swaps)} swap(s)")
    for s in swaps:
        print(f"  → {s.get('match_description', '')}")

    # 滑动可视化
    if swaps:
        w, h = image_dimensions(screenshot)
        coords_list = []
        for s in swaps:
            c = s.get("coordinates", {})
            fr, to = c.get("from", [0, 0]), c.get("to", [0, 0])
            coords_list.append((
                int(fr[0] * w / 1000), int(fr[1] * h / 1000),
                int(to[0] * w / 1000), int(to[1] * h / 1000),
            ))
        png = annotate_swipe(screenshot, *coords_list[0]) if len(coords_list) == 1 else annotate_multi_swipe(screenshot, coords_list)
        (out_dir / "clicks.png").write_bytes(png)
        print(f"→ {out_dir / 'clicks.png'}")

    print(f"\nDone: {out_dir}/")


if __name__ == "__main__":
    asyncio.run(main())

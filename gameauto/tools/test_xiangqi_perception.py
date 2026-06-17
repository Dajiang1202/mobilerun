#!/usr/bin/env python3
"""天天象棋离线批量测试 — VLM 感知 + 引擎决策 + 可视化。

对截图目录下所有 PNG 逐张:
  1. VLM 识别棋盘 + 棋子（双坐标）
  2. 解析棋盘，构建 Board
  3. 引擎搜索最优走法
  4. 记谱（如"炮二平五"）
  5. 保存可视化：perception.png（棋盘标注）+ move.png（走法箭头）+ clicks.png

用法:
    python tools/test_xiangqi_perception.py
    python tools/test_xiangqi_perception.py --dir D:/path/to/screenshots
    python tools/test_xiangqi_perception.py --dir D:/path/to/screenshots --output D:/path/to/output
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gameauto.config.loader import load_global_config
from gameauto.core.perception.vlm_client import VlmClient
from gameauto.skills.xiangqi.perception import XiangqiPerception
from gameauto.skills.xiangqi.decision import decide
from gameauto.skills.xiangqi.engine import Board, find_best_move_pikafish
from gameauto.skills.xiangqi.visualizer import annotate_board_state, annotate_move, annotate_clicks

# ═══════════════════════════════════════════════════════════════════════
# 默认配置（可通过命令行覆盖）
# ═══════════════════════════════════════════════════════════════════════

DEFAULT_INPUT = Path(r"D:\gameauto\mobilerun\gameauto\core\capture\scrcpy\captured\xiangqi_test")
DEFAULT_OUTPUT = Path(r"D:\gameauto\mobilerun\gameauto\test_vlm_output\xiangqi")


async def process_one(vlm: VlmClient, perception: XiangqiPerception,
                      image_path: Path, out_dir: Path, idx: int, total: int) -> dict:
    """处理单张截图。"""
    name = image_path.stem
    shot_dir = out_dir / name
    shot_dir.mkdir(parents=True, exist_ok=True)
    screenshot = image_path.read_bytes()
    (shot_dir / "screenshot.png").write_bytes(screenshot)

    print(f"\n{'─'*60}")
    print(f"[{idx}/{total}] {name}  ({len(screenshot)} bytes)")

    # ── VLM 感知 ──────────────────────────────────────────────────
    t0 = time.perf_counter()
    result = await perception.recognize(screenshot)
    elapsed = (time.perf_counter() - t0) * 1000

    state = result.parsed
    screen_type = state.get("screen_type", "?")
    pieces = state.get("pieces", [])
    buttons = state.get("buttons", [])

    print(f"  VLM: {elapsed:.0f}ms | screen={screen_type} | pieces={len(pieces)}")

    # 打印棋子清单（前10颗）
    for p in pieces[:10]:
        bp = p.get("board_pos", {})
        pp = p.get("pixel_pos", {})
        print(f"    {p.get('piece','?')} {p.get('side','?')} "
              f"board=({bp.get('col','?')},{bp.get('row','?')}) "
              f"pixel=({pp.get('x','?')},{pp.get('y','?')})")
    if len(pieces) > 10:
        print(f"    ... and {len(pieces)-10} more")

    # ── 感知可视化 ────────────────────────────────────────────────
    if pieces:
        png = annotate_board_state(screenshot, state)
        (shot_dir / "perception.png").write_bytes(png)
        print(f"  → perception.png")

    # ── 引擎决策 ──────────────────────────────────────────────────
    move_result = None
    actions = []
    if screen_type == "playing" and pieces:
        actions = decide(state, round_num=idx)

        # 同时直接用引擎算一遍（展示记谱）
        try:
            board = Board.from_pieces(pieces)
            move_result = find_best_move_pikafish(board, side="red")
        except Exception as e:
            print(f"  Engine error: {e}")

    # ── 决策输出 ──────────────────────────────────────────────────
    if move_result:
        print(f"  Best move: {move_result['notation']}")
        print(f"    from: board({move_result['from']['col']},{move_result['from']['row']})")
        print(f"    to:   board({move_result['to']['col']},{move_result['to']['row']})")
        if move_result.get("captured"):
            print(f"    captures: {move_result['captured']}")

    if actions:
        for a in actions:
            print(f"  Action: {a.type} {a.description} @ ({a.x1},{a.y1})" if a.x1 else f"  Action: {a.type} {a.description}")
        # 点击可视化
        (shot_dir / "clicks.png").write_bytes(annotate_clicks(screenshot, actions))
        print(f"  → clicks.png")

        # 走法箭头可视化
        taps = [(a.x1, a.y1, a.description or "") for a in actions if a.type == "tap"]
        if len(taps) >= 2:
            move_png = annotate_move(screenshot, state, taps)
            (shot_dir / "move.png").write_bytes(move_png)
            print(f"  → move.png")
    elif screen_type == "playing" and pieces:
        print(f"  ⚠ No actions generated — check board recognition!")

    # ── 摘要 ──────────────────────────────────────────────────────
    summary = {
        "file": str(image_path),
        "screen_type": screen_type,
        "pieces_count": len(pieces),
        "button_texts": [b.get("text", "") for b in buttons],
        "latency_ms": round(elapsed, 0),
        "best_move": move_result,
        "actions": [{"type": a.type, "x": a.x1, "y": a.y1, "desc": a.description} for a in actions],
        "vlm_raw": (result.raw_response or ""),
    }
    (shot_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    return summary


async def main():
    parser = argparse.ArgumentParser(description="Test Xiangqi VLM perception + decision")
    parser.add_argument("--dir", type=str, help="Screenshot directory")
    parser.add_argument("--output", type=str, help="Output directory")
    args = parser.parse_args()

    input_dir = Path(args.dir) if args.dir else DEFAULT_INPUT
    out_dir = Path(args.output) if args.output else DEFAULT_OUTPUT

    if not input_dir.exists():
        print(f"ERROR: directory not found: {input_dir}")
        print(f"Usage: python tools/test_xiangqi_perception.py --dir <path>")
        sys.exit(1)

    images = sorted(input_dir.glob("*.png")) + sorted(input_dir.glob("*.jpg"))
    if not images:
        print(f"ERROR: no PNG/JPG files in {input_dir}")
        sys.exit(1)

    total = len(images)
    print(f"Found {total} screenshots in {input_dir}")
    print(f"Output: {out_dir}\n")

    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Init VLM ───────────────────────────────────────────────────
    global_cfg = load_global_config()
    vlm_cfg = global_cfg.get("vlm", {})
    print(f"VLM: {vlm_cfg.get('model')} @ {vlm_cfg.get('base_url')}")

    vlm = VlmClient(
        model=vlm_cfg.get("model", "qwen3-vl-flash"),
        base_url=vlm_cfg.get("base_url", ""),
        api_key=vlm_cfg.get("api_key", ""),
    )
    print(f"  enable_thinking: {vlm._enable_thinking}")

    prompt_path = Path(__file__).parent.parent / "skills" / "xiangqi" / "prompts" / "xiangqi.jinja2"
    if not prompt_path.exists():
        print(f"ERROR: prompt not found: {prompt_path}")
        sys.exit(1)
    perception = XiangqiPerception.from_prompt_file(vlm, str(prompt_path))

    # ── Batch process ──────────────────────────────────────────────
    results = []
    stats = {"playing": 0, "menu": 0, "game_over": 0, "with_move": 0}

    for i, img_path in enumerate(images, 1):
        try:
            summary = await process_one(vlm, perception, img_path, out_dir, i, total)
            results.append(summary)
            st = summary["screen_type"]
            stats[st] = stats.get(st, 0) + 1
            if summary.get("best_move"):
                stats["with_move"] += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"file": str(img_path), "error": str(e)})

    # ── Summary report ────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"BATCH COMPLETE: {total} screenshots")
    print(f"{'='*60}")
    print(f"  screen_type distribution:")
    for k in ["playing", "menu", "game_over", "unknown"]:
        if stats.get(k, 0) > 0:
            print(f"    {k}: {stats[k]}")
    print(f"  with best move: {stats['with_move']}")
    print(f"\n  Output: {out_dir}/")
    print(f"  Per-shot: {out_dir}/<filename>/\n")


if __name__ == "__main__":
    asyncio.run(main())

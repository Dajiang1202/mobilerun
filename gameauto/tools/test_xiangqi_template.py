#!/usr/bin/env python3
"""天天象棋离线批量测试 —— 模板匹配感知 + 引擎决策 + 可视化。

镜像 test_xiangqi_perception.py(VLM 版), 但用 XiangqiTemplatePerception,
对一批截图逐张: 感知 → 标注棋盘 → 引擎搜索 → 决策点击 → 输出 summary。
用于验证模板/标定是否正确(Board.from_pieces 不报错、Pikafish 返回走法、
点击落在棋盘内)。

用法
    python gameauto/tools/test_xiangqi_template.py --dir D:/screenshots --output D:/test_template_output/xiangqi
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gameauto.skills.xiangqi.perception_template import XiangqiTemplatePerception
from gameauto.skills.xiangqi.decision import decide
from gameauto.skills.xiangqi.engine import Board, find_best_move_pikafish
from gameauto.skills.xiangqi.visualizer import (
    annotate_board_state, annotate_move, annotate_clicks,
)

DEFAULT_INPUT = Path(r"D:\screenshots")
DEFAULT_OUTPUT = Path(r"D:\gameauto\mobilerun\gameauto\test_template_output\xiangqi")


async def process_one(perception: XiangqiTemplatePerception,
                      image_path: Path, out_dir: Path, idx: int, total: int) -> dict:
    name = image_path.stem
    shot_dir = out_dir / name
    shot_dir.mkdir(parents=True, exist_ok=True)
    screenshot = image_path.read_bytes()
    (shot_dir / "screenshot.png").write_bytes(screenshot)

    t0 = time.perf_counter()
    result = await perception.recognize(screenshot)
    elapsed = (time.perf_counter() - t0) * 1000
    state = result.parsed
    screen_type = state.get("screen_type", "unknown")
    pieces = state.get("pieces", [])
    buttons = state.get("buttons", [])

    print(f"[{idx}/{total}] {name}  {elapsed:.0f}ms screen={screen_type} "
          f"pieces={len(pieces)} buttons={[b.get('text') for b in buttons]}")

    # 棋盘标注(visualizer 消费相同 dict, 零改)
    if pieces:
        (shot_dir / "perception.png").write_bytes(annotate_board_state(screenshot, state))

    # ── 引擎校验: from_pieces 必须成功 + Pikafish 返回走法 ──
    engine_ok = False
    move_result = None
    engine_err = None
    actions = []
    if screen_type == "playing" and pieces:
        try:
            board = Board.from_pieces(pieces, side_to_move="red")
            move_result = find_best_move_pikafish(board, side="red", movetime=2000)
            engine_ok = move_result is not None
        except Exception as e:  # noqa: BLE001
            engine_err = str(e)
        actions, _ = decide(state, idx)
        if actions:
            (shot_dir / "clicks.png").write_bytes(annotate_clicks(screenshot, actions))
            taps = [(a.x1, a.y1, a.description or "") for a in actions if a.type == "tap"]
            if len(taps) >= 2:
                (shot_dir / "move.png").write_bytes(annotate_move(screenshot, state, taps))

    red_n = sum(1 for p in pieces if p.get("side") == "red")
    black_n = len(pieces) - red_n
    summary = {
        "file": str(image_path),
        "screen_type": screen_type,
        "pieces_count": len(pieces),
        "red": red_n,
        "black": black_n,
        "buttons": [b.get("text") for b in buttons],
        "board": state.get("board"),
        "latency_ms": round(elapsed, 0),
        "engine_ok": engine_ok,
        "engine_error": engine_err,
        "best_move": move_result,
        "actions": [{"type": a.type, "x": a.x1, "y": a.y1, "desc": a.description}
                    for a in actions],
    }
    (shot_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


async def main() -> int:
    ap = argparse.ArgumentParser(description="天天象棋模板匹配批量测试")
    ap.add_argument("--dir", default=str(DEFAULT_INPUT), help="截图目录")
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出目录")
    ap.add_argument("--skill-dir",
                    default=str(Path(__file__).resolve().parent.parent
                                / "skills" / "xiangqi"),
                    help="xiangqi skill 目录(含 assets/)")
    args = ap.parse_args()

    input_dir = Path(args.dir)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    images = (sorted(input_dir.glob("*.png"))
              + sorted(input_dir.glob("*.jpg"))
              + sorted(input_dir.glob("*.jpeg")))
    if not images:
        print(f"错误: {input_dir} 下找不到截图。用 --dir 指定。")
        return 1

    perception = XiangqiTemplatePerception.from_skill_dir(args.skill_dir)
    results: list[dict] = []
    stats = {"playing": 0, "menu": 0, "game_over": 0, "unknown": 0,
             "engine_ok": 0, "with_actions": 0}
    for i, p in enumerate(images, 1):
        try:
            s = await process_one(perception, p, out_dir, i, len(images))
            results.append(s)
            stats[s["screen_type"]] = stats.get(s["screen_type"], 0) + 1
            if s["engine_ok"]:
                stats["engine_ok"] += 1
            if s["actions"]:
                stats["with_actions"] += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {p.name}: {e}")
            results.append({"file": str(p), "error": str(e)})

    (out_dir / "batch_summary.json").write_text(
        json.dumps({"stats": stats, "results": results},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDONE: {len(images)} 张 | playing={stats['playing']} menu={stats['menu']} "
          f"game_over={stats['game_over']} unknown={stats['unknown']}")
    print(f"引擎通过: engine_ok={stats['engine_ok']}/{stats['playing']}  "
          f"产出动作={stats['with_actions']}/{stats['playing']}")
    print(f"输出: {out_dir}/batch_summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

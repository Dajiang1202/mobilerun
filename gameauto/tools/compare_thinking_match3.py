#!/usr/bin/env python3
"""Match3对比实验：同一提示词，qwen3.6-plus 思考开启 vs 关闭。

对 test2/ 目录下所有截图分别用 thinking=ON 和 thinking=OFF 跑一遍，
输出棋盘可视化 + 滑动可视化 + 对比报告到 output2/ 目录。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gameauto.config.loader import load_global_config
from gameauto.core.perception.vlm_client import VlmClient
from gameauto.skills.match3.perception import Match3Perception
from gameauto.skills.match3.solver import solve_board_multi
from gameauto.skills.match3.visualizer import annotate_board, annotate_multi_swipe, annotate_swipe

# =============================================================================
INPUT_DIR = Path(r"D:\gameauto\mobilerun\gameauto\core\capture\scrcpy\captured\test2")
OUTPUT_DIR = Path(r"D:\gameauto\mobilerun\gameauto\core\capture\scrcpy\captured\output2")
MODEL = "qwen3.6-plus"
MAX_STEPS = 2
# =============================================================================


async def process_shot(
    vlm: VlmClient, perception: Match3Perception,
    image_path: Path, out_dir: Path, label: str, idx: int, total: int,
) -> dict:
    name = image_path.stem
    shot_dir = out_dir / name
    shot_dir.mkdir(parents=True, exist_ok=True)
    screenshot = image_path.read_bytes()
    (shot_dir / "screenshot.png").write_bytes(screenshot)

    t0 = time.perf_counter()

    # ---- VLM perception ----
    result = await perception.recognize(screenshot)
    board = result.parsed

    elapsed = (time.perf_counter() - t0) * 1000

    rows = board.get("rows", 0)
    cols = board.get("cols", 0)
    n_tiles = sum(len(row) for row in board.get("tiles", []))

    # ---- Board visualization ----
    if board and board.get("tiles"):
        png = annotate_board(screenshot, board)
        (shot_dir / "perception.png").write_bytes(png)

    # ---- Solver ----
    swaps = solve_board_multi(board, max_steps=MAX_STEPS) if board.get("tiles") else []
    swap_summary = [
        {"desc": s.get("match_description", ""),
         "from": s.get("coordinates", {}).get("from", []),
         "to": s.get("coordinates", {}).get("to", [])}
        for s in swaps
    ]

    # ---- Swipe visualization ----
    if swaps:
        native_w = 1080  # fallback
        try:
            from PIL import Image as PILImage
            from io import BytesIO
            img = PILImage.open(BytesIO(screenshot))
            native_w, native_h = img.size
        except:
            pass

        coords_list = []
        for s in swaps:
            c = s.get("coordinates", {})
            fr, to = c.get("from", [0, 0]), c.get("to", [0, 0])
            coords_list.append((
                int(fr[0] * native_w / 1000), int(fr[1] * native_h / 1000),
                int(to[0] * native_w / 1000), int(to[1] * native_h / 1000),
            ))

        if len(coords_list) == 1:
            swipe_png = annotate_swipe(screenshot, *coords_list[0])
        else:
            swipe_png = annotate_multi_swipe(screenshot, coords_list)
        (shot_dir / "clicks.png").write_bytes(swipe_png)

    # ---- Summary ----
    summary = {
        "file": name,
        "label": label,
        "model": MODEL,
        "latency_ms": round(elapsed, 0),
        "board": {"rows": rows, "cols": cols, "tiles_count": n_tiles},
        "swaps_found": len(swaps),
        "swaps": swap_summary,
        "vlm_raw_preview": (result.raw_response or "")[:300],
    }
    (shot_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    return summary


async def main():
    if not INPUT_DIR.exists():
        print(f"ERROR: {INPUT_DIR} not found")
        sys.exit(1)

    images = sorted(INPUT_DIR.glob("*.png"))
    if not images:
        print(f"ERROR: no PNGs in {INPUT_DIR}")
        sys.exit(1)

    total = len(images)
    print(f"Found {total} screenshots")
    print(f"Model: {MODEL}  Max steps: {MAX_STEPS}")
    print(f"Output: {OUTPUT_DIR}\n")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ---- Config ----
    global_cfg = load_global_config()
    vlm_cfg = global_cfg.get("vlm", {})
    base_url = vlm_cfg.get("base_url", "")
    api_key = vlm_cfg.get("api_key", "")

    prompt_path = Path(__file__).parent.parent / "skills" / "match3" / "prompts" / "generic.jinja2"
    prompt_text = prompt_path.read_text(encoding="utf-8")

    # ---- Two VLM instances ----
    vlm_on = VlmClient(model=MODEL, base_url=base_url, api_key=api_key, enable_thinking=True)
    vlm_off = VlmClient(model=MODEL, base_url=base_url, api_key=api_key, enable_thinking=False)

    perception_on = Match3Perception(vlm_on, prompt_text)
    perception_off = Match3Perception(vlm_off, prompt_text)

    configs = [
        ("thinking_on", vlm_on, perception_on),
        ("thinking_off", vlm_off, perception_off),
    ]

    all_results = {}

    for label, vlm, perception in configs:
        print(f"\n{'='*60}")
        print(f"  {label.upper()}  (enable_thinking={'True' if label == 'thinking_on' else 'False'})")
        print(f"{'='*60}")

        out_dir = OUTPUT_DIR / label
        out_dir.mkdir(parents=True, exist_ok=True)
        results = []

        for i, img_path in enumerate(images, 1):
            print(f"  [{i}/{total}] {img_path.name}...", end=" ", flush=True)
            try:
                summary = await process_shot(vlm, perception, img_path, out_dir, label, i, total)
                results.append(summary)

                b = summary["board"]
                swaps = summary["swaps_found"]
                print(f"board={b['rows']}x{b['cols']} | swaps={swaps} | {summary['latency_ms']:.0f}ms")

            except Exception as e:
                print(f"ERROR: {e}")
                results.append({"file": img_path.name, "label": label, "error": str(e)})

        all_results[label] = results

    # ---- Comparison report ----
    print(f"\n{'='*60}")
    print("  COMPARISON REPORT")
    print(f"{'='*60}")

    report = {"model": MODEL, "total_screenshots": total, "comparisons": []}
    stats = {"thinking_on": {"total_ms": 0, "count": 0},
             "thinking_off": {"total_ms": 0, "count": 0}}

    for i, name in enumerate([img.stem for img in images]):
        on_r = all_results["thinking_on"][i]
        off_r = all_results["thinking_off"][i]

        on_ms = on_r.get("latency_ms", 0)
        off_ms = off_r.get("latency_ms", 0)
        stats["thinking_on"]["total_ms"] += on_ms; stats["thinking_on"]["count"] += 1
        stats["thinking_off"]["total_ms"] += off_ms; stats["thinking_off"]["count"] += 1

        on_board = f"{on_r.get('board',{}).get('rows',0)}x{on_r.get('board',{}).get('cols',0)}"
        off_board = f"{off_r.get('board',{}).get('rows',0)}x{off_r.get('board',{}).get('cols',0)}"
        on_swaps = on_r.get("swaps_found", 0)
        off_swaps = off_r.get("swaps_found", 0)

        board_match = "MATCH" if on_board == off_board else "DIFF"
        swaps_match = "MATCH" if on_swaps == off_swaps else "DIFF"

        print(f"\n  {name}:")
        print(f"    board:  ON={on_board}  OFF={off_board}  {board_match}")
        print(f"    swaps:  ON={on_swaps}  OFF={off_swaps}  {swaps_match}")
        print(f"    latency: ON={on_ms:.0f}ms  OFF={off_ms:.0f}ms  diff={on_ms - off_ms:+.0f}ms")

        on_swap_descs = [s.get("desc","")[:50] for s in on_r.get("swaps",[])]
        off_swap_descs = [s.get("desc","")[:50] for s in off_r.get("swaps",[])]
        if on_swap_descs != off_swap_descs:
            print(f"    ON  swaps: {on_swap_descs}")
            print(f"    OFF swaps: {off_swap_descs}")

        report["comparisons"].append({
            "file": name,
            "thinking_on": {"board": on_board, "swaps": on_swaps, "latency_ms": on_ms},
            "thinking_off": {"board": off_board, "swaps": off_swaps, "latency_ms": off_ms},
            "board_match": on_board == off_board,
            "swaps_match": on_swaps == off_swaps,
        })

    # Summary
    on_avg = stats["thinking_on"]["total_ms"] / stats["thinking_on"]["count"] if stats["thinking_on"]["count"] else 0
    off_avg = stats["thinking_off"]["total_ms"] / stats["thinking_off"]["count"] if stats["thinking_off"]["count"] else 0
    board_matches = sum(1 for c in report["comparisons"] if c["board_match"])
    swaps_matches = sum(1 for c in report["comparisons"] if c["swaps_match"])

    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"  Board match:  {board_matches}/{total}")
    print(f"  Swaps match:  {swaps_matches}/{total}")
    print(f"  Avg latency ON:   {on_avg:.0f}ms")
    print(f"  Avg latency OFF:  {off_avg:.0f}ms")
    print(f"  Thinking overhead: {on_avg - off_avg:+.0f}ms")

    report["summary"] = {
        "board_match": f"{board_matches}/{total}",
        "swaps_match": f"{swaps_matches}/{total}",
        "avg_latency_on_ms": round(on_avg, 0),
        "avg_latency_off_ms": round(off_avg, 0),
        "latency_delta_ms": round(on_avg - off_avg, 0),
    }

    (OUTPUT_DIR / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"\n  Report: {OUTPUT_DIR / 'report.json'}")
    print(f"  Details: {OUTPUT_DIR}/thinking_on/  &  thinking_off/")


if __name__ == "__main__":
    asyncio.run(main())

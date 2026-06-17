#!/usr/bin/env python3
"""对比实验：同一提示词，qwen3-vl-plus 思考开启 vs 关闭。

对 test/ 目录下所有截图分别用 thinking=ON 和 thinking=OFF 跑一遍，
输出感知可视化 + 点击可视化 + 对比报告到 output/ 目录。

输出结构:
    output/
      thinking_on/shot_XXXX/    perception.png, clicks.png, summary.json
      thinking_off/shot_XXXX/   perception.png, clicks.png, summary.json
      report.json               对比汇总
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
from gameauto.skills.doudizhu.perception import DouDiZhuPerception
from gameauto.skills.doudizhu.decision import decide, reset_continue_count
from gameauto.skills.doudizhu.visualizer import annotate_game_state, annotate_clicks

# ═══════════════════════════════════════════════════════════════════════
INPUT_DIR = Path(r"D:\gameauto\mobilerun\gameauto\core\capture\scrcpy\captured\test")
OUTPUT_DIR = Path(r"D:\gameauto\mobilerun\gameauto\core\capture\scrcpy\captured\output")
MODEL = "qwen3.6-plus"
# ═══════════════════════════════════════════════════════════════════════


async def process_shot(
    vlm: VlmClient,
    perception: DouDiZhuPerception,
    image_path: Path,
    out_dir: Path,
    label: str,
    idx: int,
    total: int,
) -> dict:
    """处理单张截图，返回摘要。"""
    name = image_path.stem
    shot_dir = out_dir / name
    shot_dir.mkdir(parents=True, exist_ok=True)
    screenshot = image_path.read_bytes()
    (shot_dir / "screenshot.png").write_bytes(screenshot)

    t0 = time.perf_counter()

    # ── VLM 感知 ──────────────────────────────────────────────────
    result = await perception.recognize(screenshot)
    state = result.parsed

    elapsed = (time.perf_counter() - t0) * 1000

    screen_type = state.get("screen_type", "?")
    buttons = state.get("buttons", [])
    cards = state.get("hand_cards", [])
    button_texts = [b.get("text", "?") for b in buttons]

    # ── 感知可视化 ────────────────────────────────────────────────
    if state:
        png = annotate_game_state(screenshot, state)
        (shot_dir / "perception.png").write_bytes(png)

    # ── 决策 ──────────────────────────────────────────────────────
    reset_continue_count()
    actions = decide(state, round_num=idx)
    action_summary = [
        {"type": a.type, "x": a.x1, "y": a.y1, "desc": a.description}
        for a in actions if a.type != "wait"
    ]

    # ── 点击可视化 ────────────────────────────────────────────────
    if actions:
        perception_path = shot_dir / "perception.png"
        base_img = perception_path.read_bytes() if perception_path.exists() else screenshot
        png = annotate_clicks(base_img, actions)
        (shot_dir / "clicks.png").write_bytes(png)

    # ── 摘要 ──────────────────────────────────────────────────────
    summary = {
        "file": name,
        "label": label,
        "model": MODEL,
        "latency_ms": round(elapsed, 0),
        "screen_type": screen_type,
        "buttons": [{"text": b.get("text"), "color": b.get("color"), "x": b.get("x"), "y": b.get("y")} for b in buttons],
        "hand_cards_count": len(cards),
        "hand_cards_first5": [
            {"suit": c.get("suit","?")[0], "value": str(c.get("value","?")), "x": c.get("x"), "y": c.get("y")}
            for c in cards[:5]
        ],
        "actions": action_summary,
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
    print(f"Model: {MODEL}")
    print(f"Output: {OUTPUT_DIR}\n")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── 加载配置和提示词 ──────────────────────────────────────────
    global_cfg = load_global_config()
    vlm_cfg = global_cfg.get("vlm", {})
    base_url = vlm_cfg.get("base_url", "")
    api_key = vlm_cfg.get("api_key", "")

    prompt_path = Path(__file__).parent.parent / "skills" / "doudizhu" / "prompts" / "doudizhu.jinja2"
    prompt_text = prompt_path.read_text(encoding="utf-8")

    # ── 两个 VLM 实例 ─────────────────────────────────────────────
    vlm_on = VlmClient(model=MODEL, base_url=base_url, api_key=api_key, enable_thinking=True)
    vlm_off = VlmClient(model=MODEL, base_url=base_url, api_key=api_key, enable_thinking=False)

    perception_on = DouDiZhuPerception(vlm_on, prompt_text)
    perception_off = DouDiZhuPerception(vlm_off, prompt_text)

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

                # 简要输出
                st = summary["screen_type"]
                btns = [b["text"] for b in summary["buttons"]]
                acts = [a["desc"] for a in summary["actions"]]
                print(f"screen={st} | btns={btns} | acts={acts} | {summary['latency_ms']:.0f}ms")

            except Exception as e:
                print(f"ERROR: {e}")
                results.append({"file": img_path.name, "label": label, "error": str(e)})

        all_results[label] = results

    # ── 对比报告 ──────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  COMPARISON REPORT")
    print(f"{'='*60}")

    report = {
        "model": MODEL,
        "total_screenshots": total,
        "comparisons": [],
    }

    stats = {"thinking_on": {"total_ms": 0, "count": 0},
             "thinking_off": {"total_ms": 0, "count": 0}}

    for i, name in enumerate([img.stem for img in images]):
        on_r = all_results["thinking_on"][i]
        off_r = all_results["thinking_off"][i]

        on_ms = on_r.get("latency_ms", 0)
        off_ms = off_r.get("latency_ms", 0)
        stats["thinking_on"]["total_ms"] += on_ms
        stats["thinking_on"]["count"] += 1
        stats["thinking_off"]["total_ms"] += off_ms
        stats["thinking_off"]["count"] += 1

        on_screen = on_r.get("screen_type", "?")
        off_screen = off_r.get("screen_type", "?")
        on_btns = [b["text"] for b in on_r.get("buttons", [])]
        off_btns = [b["text"] for b in off_r.get("buttons", [])]
        on_acts = [a["desc"] for a in on_r.get("actions", [])]
        off_acts = [a["desc"] for a in off_r.get("actions", [])]

        screen_match = "MATCH" if on_screen == off_screen else "DIFF"
        btns_match = "MATCH" if on_btns == off_btns else "DIFF"
        acts_match = "MATCH" if on_acts == off_acts else "DIFF"

        print(f"\n  {name}:")
        print(f"    screen_type:  ON={on_screen}  OFF={off_screen}  {screen_match}")
        print(f"    buttons:      ON={on_btns}")
        print(f"                  OFF={off_btns}  {btns_match}")
        print(f"    actions:      ON={on_acts}")
        print(f"                  OFF={off_acts}  {acts_match}")
        print(f"    latency:      ON={on_ms:.0f}ms  OFF={off_ms:.0f}ms  diff={on_ms - off_ms:+.0f}ms")

        report["comparisons"].append({
            "file": name,
            "thinking_on": {
                "screen_type": on_screen,
                "buttons": on_btns,
                "actions": on_acts,
                "latency_ms": on_ms,
            },
            "thinking_off": {
                "screen_type": off_screen,
                "buttons": off_btns,
                "actions": off_acts,
                "latency_ms": off_ms,
            },
            "screen_type_match": on_screen == off_screen,
            "buttons_match": on_btns == off_btns,
            "actions_match": on_acts == off_acts,
        })

    # 汇总统计
    on_avg = stats["thinking_on"]["total_ms"] / stats["thinking_on"]["count"] if stats["thinking_on"]["count"] else 0
    off_avg = stats["thinking_off"]["total_ms"] / stats["thinking_off"]["count"] if stats["thinking_off"]["count"] else 0
    match_count = sum(1 for c in report["comparisons"] if c["actions_match"])

    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"  Actions match:     {match_count}/{total}")
    print(f"  Avg latency ON:    {on_avg:.0f}ms")
    print(f"  Avg latency OFF:   {off_avg:.0f}ms")
    print(f"  Latency delta:     {on_avg - off_avg:+.0f}ms (thinking overhead)")

    report["summary"] = {
        "actions_match": f"{match_count}/{total}",
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

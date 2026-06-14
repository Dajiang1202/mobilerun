#!/usr/bin/env python3
"""批量截图测试 — 对 captured/ 目录下所有截图逐一跑 VLM 感知 + 决策 + 可视化。

输出目录: gameauto/test_vlm_output/batch/
每个截图生成:
    {name}/
      perception.png    VLM 感知可视化（按钮颜色框 + 手牌标注）
      clicks.png        点击序列可视化（编号圆圈 + 操作描述）
      summary.json      VLM 原始返回 + 解析结果 + 决策操作
      screenshot.png    原始截图副本
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gameauto.config.loader import load_global_config
from gameauto.core.perception.vlm_client import VlmClient
from gameauto.skills.doudizhu.perception import DouDiZhuPerception
from gameauto.skills.doudizhu.decision import decide
from gameauto.skills.doudizhu.visualizer import annotate_game_state, annotate_clicks

# ═══════════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════════

CAPTURED_DIR = Path(r"D:\gameauto\mobilerun\gameauto\core\capture\scrcpy\captured")
OUT_DIR = Path(__file__).parent.parent / "test_vlm_output" / "batch"


async def process_one(vlm: VlmClient, perception: DouDiZhuPerception,
                      image_path: Path, out_dir: Path, idx: int, total: int) -> dict:
    """处理单张截图，返回摘要 dict。"""
    name = image_path.stem
    shot_dir = out_dir / name
    shot_dir.mkdir(parents=True, exist_ok=True)

    screenshot = image_path.read_bytes()
    (shot_dir / "screenshot.png").write_bytes(screenshot)

    print(f"\n{'─'*60}")
    print(f"[{idx}/{total}] {name}  ({len(screenshot)} bytes)")

    # ── VLM 感知 ──────────────────────────────────────────────────
    result = await perception.recognize(screenshot)
    state = result.parsed

    screen_type = state.get("screen_type", "?")
    buttons = state.get("buttons", [])
    cards = state.get("hand_cards", [])
    button_texts = [b.get("text", "?") for b in buttons]

    print(f"  screen_type: {screen_type}")
    print(f"  buttons ({len(buttons)}): {button_texts}")
    for b in buttons:
        print(f"    [{b.get('text','?')}] color={b.get('color','?')}  ({b['x']},{b['y']})")
    print(f"  cards: {len(cards)}")

    # ── 感知可视化 ────────────────────────────────────────────────
    if state:
        png = annotate_game_state(screenshot, state)
        (shot_dir / "perception.png").write_bytes(png)

    # ── 决策 ──────────────────────────────────────────────────────
    actions = decide(state, round_num=1)  # 测试用固定回合数
    action_summary = [
        {"type": a.type, "x": a.x1, "y": a.y1, "desc": a.description}
        for a in actions
    ]
    print(f"  actions ({len(actions)}):")
    for a in action_summary:
        print(f"    → {a['desc']} @ ({a['x']},{a['y']})")

    # ── 点击可视化 ────────────────────────────────────────────────
    if actions:
        perception_path = shot_dir / "perception.png"
        base_img = perception_path.read_bytes() if perception_path.exists() else screenshot
        png = annotate_clicks(base_img, actions)
        (shot_dir / "clicks.png").write_bytes(png)

    # ── 保存摘要 ──────────────────────────────────────────────────
    summary = {
        "file": str(image_path),
        "screen_type": screen_type,
        "buttons": buttons,
        "hand_cards_count": len(cards),
        "actions": action_summary,
        "vlm_raw": result.raw_response[:500] if result.raw_response else "",
    }
    (shot_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    return summary


async def main():
    # ── 检查截图目录 ──────────────────────────────────────────────
    if not CAPTURED_DIR.exists():
        print(f"ERROR: directory not found: {CAPTURED_DIR}")
        sys.exit(1)

    images = sorted(CAPTURED_DIR.glob("*.png"))
    if not images:
        print(f"ERROR: no PNG files in {CAPTURED_DIR}")
        sys.exit(1)

    print(f"Found {len(images)} screenshots in {CAPTURED_DIR}")
    print(f"Output: {OUT_DIR}\n")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── 初始化 VLM ────────────────────────────────────────────────
    global_cfg = load_global_config()
    vlm_cfg = global_cfg.get("vlm", {})
    print(f"VLM: {vlm_cfg.get('model')} @ {vlm_cfg.get('base_url')}")

    vlm = VlmClient(
        model=vlm_cfg.get("model", "gpt-4o"),
        base_url=vlm_cfg.get("base_url", ""),
        api_key=vlm_cfg.get("api_key", ""),
    )

    # ── 初始化感知 ────────────────────────────────────────────────
    prompt_path = Path(__file__).parent.parent / "skills" / "doudizhu" / "prompts" / "doudizhu.jinja2"
    if not prompt_path.exists():
        print(f"ERROR: prompt not found: {prompt_path}")
        sys.exit(1)
    perception = DouDiZhuPerception.from_prompt_file(vlm, str(prompt_path))

    # ── 批量处理 ──────────────────────────────────────────────────
    total = len(images)
    results = []
    stats = {"start": 0, "waiting": 0, "playing": 0, "unknown": 0, "with_actions": 0, "no_actions": 0}

    for i, img_path in enumerate(images, 1):
        try:
            summary = await process_one(vlm, perception, img_path, OUT_DIR, i, total)
            results.append(summary)

            st = summary["screen_type"]
            stats[st] = stats.get(st, 0) + 1
            if summary["actions"]:
                stats["with_actions"] += 1
            else:
                stats["no_actions"] += 1

        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"file": str(img_path), "error": str(e)})

    # ── 汇总报告 ──────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"BATCH COMPLETE: {total} screenshots")
    print(f"{'='*60}")
    print(f"  screen_type distribution:")
    for k in ["start", "waiting", "playing", "unknown"]:
        if stats.get(k, 0) > 0:
            print(f"    {k}: {stats[k]}")
    print(f"  with actions:    {stats['with_actions']}")
    print(f"  without actions: {stats['no_actions']}")

    # 保存全局汇总
    report = {
        "total": total,
        "stats": {k: v for k, v in stats.items()},
        "results": [
            {
                "file": Path(r["file"]).name if "file" in r else "?",
                "screen_type": r.get("screen_type", "error"),
                "buttons": [b.get("text") for b in r.get("buttons", [])],
                "actions": [a["desc"] for a in r.get("actions", [])],
            }
            for r in results
        ],
    }
    (OUT_DIR / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"\nFull report: {OUT_DIR / 'report.json'}")
    print(f"Per-shot details: {OUT_DIR}/shot_XXXX/")


if __name__ == "__main__":
    asyncio.run(main())

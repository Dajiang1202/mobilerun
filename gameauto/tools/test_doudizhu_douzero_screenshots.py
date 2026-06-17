#!/usr/bin/env python3
"""批量截图测试 — DouZero 斗地主版本。

对 captured/ 目录下所有截图逐一跑 CV 感知 + DouZero 决策 + 可视化。
无需 VLM，无需实机连接 —— 纯离线测试。

输出目录: gameauto/test_vlm_output/doudizhu_douzero/
每个截图生成:
    {name}/
      perception.png     CV 模板匹配可视化（卡片框 + 按钮圈）
      actions.png        点击序列可视化（编号圆圈 + 操作描述）
      summary.json       感知结果 + 决策操作 + 耗时
      screenshot.png     原始截图副本

用法:
    # 默认测试 captured/ 目录
    python gameauto/tools/test_doudizhu_douzero_screenshots.py

    # 指定目录
    python gameauto/tools/test_doudizhu_douzero_screenshots.py --dir D:/screenshots/

    # 单张测试
    python gameauto/tools/test_doudizhu_douzero_screenshots.py --single D:/path/to/screenshot.png
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gameauto.config.loader import load_game_config
from gameauto.skills.doudizhu_douzero.perception import DouDiZhuDouzeroPerception
from gameauto.skills.doudizhu_douzero.decision import DouzeroDecision
from gameauto.skills.doudizhu_douzero.visualizer import annotate_perception, annotate_actions

# ═══════════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════════

CAPTURED_DIR = Path(__file__).parent.parent / "core" / "capture" / "scrcpy" / "captured"
OUT_DIR = Path(__file__).parent.parent / "test_vlm_output" / "doudizhu_douzero"
SKILL_DIR = Path(__file__).parent.parent / "skills" / "doudizhu_douzero"


async def process_one(
    perception: DouDiZhuDouzeroPerception,
    decision: DouzeroDecision,
    image_path: Path,
    out_dir: Path,
    idx: int,
    total: int,
) -> dict:
    """处理单张截图，返回摘要 dict。"""
    name = image_path.stem
    shot_dir = out_dir / name
    shot_dir.mkdir(parents=True, exist_ok=True)

    screenshot = image_path.read_bytes()
    (shot_dir / "screenshot.png").write_bytes(screenshot)

    print(f"\n{'─'*60}")
    print(f"[{idx}/{total}] {name}  ({len(screenshot):,} bytes)")

    # ── CV 感知 ──────────────────────────────────────────────────
    t0 = time.time()
    try:
        result = await perception.recognize(screenshot)
        state = result.parsed
        latency_ms = (time.time() - t0) * 1000
    except Exception as e:
        print(f"  ERROR: perception failed: {e}")
        return {"file": str(image_path), "error": str(e)}

    phase = state.get("phase", "?")
    buttons = state.get("buttons", [])
    button_names = state.get("button_names", [])
    my_hand = state.get("my_hand", [])
    last_play = state.get("last_play", [])
    is_pass = state.get("is_pass", False)
    is_landlord = state.get("is_landlord", False)
    card_positions = state.get("card_positions", {})

    print(f"  phase: {phase}  |  percept: {latency_ms:.0f}ms")
    print(f"  hand cards: {len(my_hand)}  {my_hand[:10]}{'...' if len(my_hand) > 10 else ''}")
    print(f"  last play:  {len(last_play)}  {last_play}")
    print(f"  is_pass: {is_pass}  |  is_landlord: {is_landlord}")
    print(f"  buttons ({len(buttons)}): {button_names}")
    for b in buttons:
        print(f"    [{b.get('text', '?')}] @ ({b['x']},{b['y']})")
    print(f"  card positions tracked: {len(card_positions)}")

    # ── 感知可视化 ────────────────────────────────────────────────
    if state:
        try:
            png = annotate_perception(screenshot, state)
            (shot_dir / "perception.png").write_bytes(png)
        except Exception as e:
            print(f"  WARN: perception viz failed: {e}")

    # ── 决策 ──────────────────────────────────────────────────────
    t0 = time.time()
    try:
        actions = decision.decide(state)
        decision_latency_ms = (time.time() - t0) * 1000
    except Exception as e:
        print(f"  ERROR: decision failed: {e}")
        actions = []
        decision_latency_ms = 0

    action_summary = [
        {"type": a.type, "x": a.x1, "y": a.y1, "desc": a.description}
        for a in actions
    ]
    print(f"  decision: {decision_latency_ms:.0f}ms")
    print(f"  actions ({len(actions)}):")
    for a in action_summary:
        print(f"    → {a['desc']} @ ({a['x']},{a['y']})")

    # ── 点击可视化 ────────────────────────────────────────────────
    if actions:
        try:
            perception_path = shot_dir / "perception.png"
            base_img = perception_path.read_bytes() if perception_path.exists() else screenshot
            png = annotate_actions(base_img, actions)
            (shot_dir / "actions.png").write_bytes(png)
        except Exception as e:
            print(f"  WARN: action viz failed: {e}")

    # ── 保存摘要 ──────────────────────────────────────────────────
    summary = {
        "file": str(image_path),
        "phase": phase,
        "perception_latency_ms": round(latency_ms, 1),
        "decision_latency_ms": round(decision_latency_ms, 1),
        "hand_cards": my_hand,
        "hand_count": len(my_hand),
        "last_play": last_play,
        "is_pass": is_pass,
        "is_landlord": is_landlord,
        "card_positions_count": len(card_positions),
        "buttons": buttons,
        "button_names": button_names,
        "actions": action_summary,
        "round_over": decision.is_round_over,
        "winner": decision.winner,
    }
    (shot_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    return summary


async def main():
    parser = argparse.ArgumentParser(description="Batch test DouDiZhu DouZero screenshots")
    parser.add_argument("--dir", type=str, help="Screenshot directory")
    parser.add_argument("--single", type=str, help="Test a single screenshot file")
    parser.add_argument("--out", type=str, help="Output directory")
    args = parser.parse_args()

    # ── 确定输入来源 ──────────────────────────────────────────────
    if args.single:
        single_path = Path(args.single)
        if not single_path.exists():
            print(f"ERROR: file not found: {single_path}")
            sys.exit(1)
        images = [single_path]
        source_label = str(single_path)
    elif args.dir:
        src_dir = Path(args.dir)
        if not src_dir.exists():
            print(f"ERROR: directory not found: {src_dir}")
            sys.exit(1)
        images = sorted(src_dir.glob("*.png"))
        source_label = str(src_dir)
    else:
        if not CAPTURED_DIR.exists():
            print(f"ERROR: default captured/ dir not found: {CAPTURED_DIR}")
            print("  Use --dir or --single to specify input")
            sys.exit(1)
        images = sorted(CAPTURED_DIR.glob("*.png"))
        source_label = str(CAPTURED_DIR)

    if not images:
        print(f"ERROR: no PNG files found")
        sys.exit(1)

    out_dir = Path(args.out) if args.out else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 加载配置 ──────────────────────────────────────────────────
    game_cfg = load_game_config("doudizhu_douzero")
    template_dir = SKILL_DIR / "assets" / "templates"
    model_dir = game_cfg.get("model_dir", "D:/resource/douzero")

    print(f"Source:    {source_label}")
    print(f"Screenshots: {len(images)}")
    print(f"Templates: {template_dir}")
    print(f"Models:    {model_dir}")
    print(f"Output:    {out_dir}")
    print()

    # ── 检查模型 ──────────────────────────────────────────────────
    model_path = Path(model_dir)
    missing = [f"{p}.ckpt" for p in ["landlord", "landlord_up", "landlord_down"]
               if not (model_path / f"{p}.ckpt").exists()]
    if missing:
        print(f"WARNING: Models not found: {missing}")
        print(f"  决策将无法工作，仅做感知测试")
        decision = None
    else:
        decision = DouzeroDecision(model_dir=model_dir)

    # 检查模板
    templates_exist = (template_dir / "cards").is_dir() or (template_dir / "buttons").is_dir()
    if not templates_exist:
        print(f"WARNING: No template images found in {template_dir}")
        print(f"  CV 感知匹配不到任何东西，输出将为空")


    # ── 初始化感知 ────────────────────────────────────────────────
    perception = DouDiZhuDouzeroPerception(
        template_dir=str(template_dir),
        card_confidence=game_cfg.get("card_confidence", 0.85),
        button_confidence=game_cfg.get("template_confidence", 0.90),
        pass_confidence=game_cfg.get("pass_confidence", 0.90),
    )

    # ── 初始化决策（空壳，无模型时只做感知可视化）─────────────────
    if decision is None:
        from gameauto.skills.doudizhu_douzero.decision import DouzeroDecision
        decision = DouzeroDecision.__new__(DouzeroDecision)
        decision._round_initialized = False
        # Monkey-patch decide to return empty
        def noop_decide(state):
            return []
        decision.decide = noop_decide
        decision.is_round_over = False
        decision.winner = None

    # ── 批量处理 ──────────────────────────────────────────────────
    total = len(images)
    results = []
    stats = {
        "bidding": 0, "playing": 0, "settlement": 0, "unknown": 0,
        "error": 0, "with_actions": 0, "no_actions": 0,
    }

    for i, img_path in enumerate(images, 1):
        try:
            summary = await process_one(perception, decision, img_path, out_dir, i, total)
            results.append(summary)

            if "error" in summary:
                stats["error"] += 1
            else:
                ph = summary.get("phase", "unknown")
                stats[ph] = stats.get(ph, 0) + 1
                if summary.get("actions"):
                    stats["with_actions"] += 1
                else:
                    stats["no_actions"] += 1

        except Exception as e:
            print(f"  FATAL ERROR: {e}")
            results.append({"file": str(img_path), "error": str(e)})
            stats["error"] += 1

    # ── 汇总报告 ──────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"BATCH COMPLETE: {total} screenshots")
    print(f"{'='*60}")
    print(f"  phase distribution:")
    for k in ["bidding", "playing", "settlement", "unknown"]:
        if stats.get(k, 0) > 0:
            print(f"    {k}: {stats[k]}")
    if stats["error"]:
        print(f"    error: {stats['error']}")
    print(f"  with actions:    {stats['with_actions']}")
    print(f"  without actions: {stats['no_actions']}")

    # 保存全局报告
    report = {
        "total": total,
        "source": source_label,
        "stats": {k: v for k, v in stats.items()},
        "results": [
            {
                "file": Path(r["file"]).name if "file" in r else "?",
                "phase": r.get("phase", "error"),
                "hand_count": r.get("hand_count", 0),
                "buttons": r.get("button_names", []),
                "actions": [a["desc"] for a in r.get("actions", [])],
                "latency": {
                    "perception_ms": r.get("perception_latency_ms", 0),
                    "decision_ms": r.get("decision_latency_ms", 0),
                },
                "error": r.get("error"),
            }
            for r in results
        ],
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"\nFull report: {out_dir / 'report.json'}")
    print(f"Per-shot details: {out_dir}/<screenshot_name>/")
    print(f"\n→ 确认截图测试通过后，运行 python gameauto/run_doudizhu_douzero.py 连接真机")


if __name__ == "__main__":
    asyncio.run(main())

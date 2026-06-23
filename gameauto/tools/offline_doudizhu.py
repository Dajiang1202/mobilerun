#!/usr/bin/env python3
"""斗地主离线单步决策 —— 用录制截图离线跑 perception → decision, 可视化决策步骤。

每张截图独立决策(不依赖连续局状态):
  - bidding/settlement: 纯按钮决策(点「不叫」/「继续」等), 不需要模型
  - playing: 用本帧识别的手牌+底牌临时 init env, 若轮到自己则调 DouZero 给出牌建议
              (非地主或轮到对手时单步无法决策, 标注原因)

决策步骤可视化: 在图上用紫色序号圈标出每个 tap 的位置(选牌/按钮), 附描述文字。
输出: {stem}_decision.png + {stem}_decision.json, 并打印每帧决策。

用法
  python gameauto/tools/offline_doudizhu.py
  python gameauto/tools/offline_doudizhu.py --limit 5
  python gameauto/tools/offline_doudizhu.py --model-dir D:/resource/douzero
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from gameauto.skills.doudizhu_douzero.perception import DouDiZhuDouzeroPerception
from gameauto.skills.doudizhu_douzero.decision import DouzeroDecision

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "skills" / "doudizhu_douzero" / "assets" / "templates"
SCREEN_DIR = Path("D:/screenshots")
OUT_DIR = Path(__file__).resolve().parent.parent / "test_vlm_output" / "doudizhu_decision"
DEFAULT_MODEL_DIR = Path("D:/resource/douzero")

logging.basicConfig(level=logging.WARNING)


def load_font(size: int):
    for p in ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"]:
        try:
            return ImageFont.truetype(p, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


async def decide_one(perception: DouDiZhuDouzeroPerception, decision: DouzeroDecision,
                     img_path: Path) -> dict:
    img_bytes = img_path.read_bytes()
    img_bgr = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)

    result = await perception.recognize(img_bytes)
    state = result.parsed
    phase = state.get("phase", "playing")
    latency = result.latency_ms

    decision.reset()
    actions = []
    note = ""

    if phase == "bidding":
        actions = decision._decide_bidding(state.get("buttons", []))
        note = "叫牌" if actions else "未识别到叫牌按钮"
    elif phase == "settlement":
        actions = decision._decide_settlement(state.get("buttons", []))
        note = "结算-继续" if actions else "未识别到「继续」"
    elif phase == "playing":
        my_hand = state.get("my_hand", [])
        lc = state.get("landlord_cards", [])
        if not my_hand:
            note = "无手牌, 跳过"
        else:
            pos = "landlord" if (state.get("is_landlord") or len(my_hand) >= 20) else "landlord_down"
            try:
                decision.init_round(my_hand, lc, pos)
                actions = decision.decide(state)
                if decision._env is not None:
                    acting = decision._env._acting_player_position
                    if acting != pos and not actions:
                        note = f"轮到对手({acting}), 单步暂不决策(需对手先手)"
                    elif actions:
                        note = f"建议出牌(位置={pos})"
                    else:
                        note = f"建议: 不出(位置={pos})"
            except FileNotFoundError as e:
                note = f"模型缺失: {e.filename}"
            except Exception as e:  # noqa: BLE001
                note = f"决策失败: {e}"

    # 可视化(Action 坐标是 [0-1000] 归一化, 画图转回像素)
    pil = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil)
    font = load_font(26)
    big = load_font(40)
    pw, ph = pil.size
    draw.rectangle([0, 0, pw, 44], fill=(10, 10, 60))
    draw.text((8, 8), f"[{phase}] {note}", fill=(255, 255, 255), font=font)
    for i, a in enumerate(actions):
        x, y = int(a.x1 * pw / 1000), int(a.y1 * ph / 1000)
        draw.ellipse([x - 38, y - 38, x + 38, y + 38], outline=(200, 0, 220), width=5)
        draw.text((x - 12, y - 22), str(i + 1), fill=(200, 0, 220), font=big)
        draw.text((x + 44, y - 14), a.description, fill=(255, 255, 0), font=font)
    pil.save(str(OUT_DIR / (img_path.stem + "_decision.png")))

    return {
        "screenshot": img_path.name,
        "phase": phase,
        "latency_ms": latency,
        "my_hand": state.get("my_hand", []),
        "last_play": state.get("last_play", []),
        "landlord_cards": state.get("landlord_cards", []),
        "is_landlord": state.get("is_landlord", False),
        "is_pass": state.get("is_pass", False),
        "buttons": [b["text"] for b in state.get("buttons", [])],
        "note": note,
        "actions": [{"step": i + 1, "type": a.type, "x": a.x1, "y": a.y1,
                     "desc": a.description} for i, a in enumerate(actions)],
    }


async def main():
    ap = argparse.ArgumentParser(description="斗地主离线单步决策")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--model-dir", default=str(DEFAULT_MODEL_DIR))
    ap.add_argument("--screens", default=str(SCREEN_DIR))
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    perception = DouDiZhuDouzeroPerception(str(TEMPLATE_DIR))
    decision = DouzeroDecision(args.model_dir)
    model_ok = (Path(args.model_dir) / "landlord.ckpt").exists()
    print(f"模板: {TEMPLATE_DIR}\n模型: {args.model_dir} ({'OK' if model_ok else '缺失! playing 决策会失败'})")
    print(f"输出: {OUT_DIR}\n")

    shots = sorted(Path(args.screens).glob("*.jpeg"))
    if args.limit:
        shots = shots[: args.limit]
    print(f"待处理 {len(shots)} 张\n")

    times: list[float] = []
    t_all = time.perf_counter()
    for i, p in enumerate(shots, 1):
        t0 = time.perf_counter()
        r = await decide_one(perception, decision, p)
        dt = time.perf_counter() - t0
        times.append(dt)
        (OUT_DIR / (p.stem + "_decision.json")).write_text(
            json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        steps = " → ".join(f"{a['step']}:{a['desc']}" for a in r["actions"]) or "(无)"
        hand = "".join(r["my_hand"])
        print(f"[{i}/{len(shots)}] {p.name} ({dt*1000:.0f}ms)")
        print(f"     手牌:{hand} | 对手:{''.join(r['last_play'])} | 底牌:{''.join(r['landlord_cards'])}"
              f" | 按钮:{r['buttons']}")
        print(f"     => {r['note']} | 决策: {steps}")
    total = time.perf_counter() - t_all
    print(f"\n=== {len(shots)} 张 | 总 {total:.1f}s | 平均 {total/len(shots)*1000:.0f}ms/张"
          f" | 最快 {min(times)*1000:.0f}ms | 最慢 {max(times)*1000:.0f}ms ===")


if __name__ == "__main__":
    asyncio.run(main())

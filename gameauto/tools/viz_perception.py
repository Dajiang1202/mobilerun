#!/usr/bin/env python3
"""批量可视化感知+决策结果 —— 对目录所有截图跑感知后端, 画框+动作箭头, 存 viz/。

BACKEND="decide" 时还会跑 rule_decide, 把要执行的动作(橙圆点/箭头)画出来。
改顶部配置区即可。用法: python gameauto/tools/viz_perception.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import gameauto.run_tft_replay as m
from gameauto.tools.cv_text import overlay_text, put_text_zh
from gameauto.utils.coordinate import to_absolute
from gameauto.skills.tft.actions import TftActions

# ═══════════════════════════════════════════════════════════════════════
#  配置区
# ═══════════════════════════════════════════════════════════════════════

SRC_DIR = r"D:\gameauto\mobilerun\gameauto\core\capture\scrcpy\captured"
BACKEND = "decide"   # "decide"(感知+5条规则动作) | "full" | "text" | "champions"
DRAW_ACTIONS = True  # decide 后端时, 把 rule_decide 的动作画出来(橙)

# ═══════════════════════════════════════════════════════════════════════


def draw_action(disp, a, fw, fh):
    """画一个 Action: tap=橙圆点, swipe/drag=橙箭头。归一化[0-1000]→帧像素。"""
    if a.type == "tap" and a.x1 is not None and a.y1 is not None:
        px, py = to_absolute(a.x1, a.y1, fw, fh)
        cv2.circle(disp, (px, py), 12, (0, 165, 255), -1)
        cv2.circle(disp, (px, py), 12, (255, 255, 255), 1)
        if a.description:
            disp = put_text_zh(disp, a.description, (px + 14, py - 10),
                               color_bgr=(0, 165, 255), px=18)
    elif a.type in ("swipe", "drag") and None not in (a.x1, a.y1, a.x2, a.y2):
        p1 = to_absolute(a.x1, a.y1, fw, fh)
        p2 = to_absolute(a.x2, a.y2, fw, fh)
        cv2.arrowedLine(disp, p1, p2, (0, 165, 255), 3, tipLength=0.2)
        cv2.circle(disp, p1, 6, (0, 165, 255), -1)
        if a.description:
            disp = put_text_zh(disp, a.description, (p1[0], p1[1] - 24),
                               color_bgr=(0, 165, 255), px=18)
    return disp


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass
    src = Path(SRC_DIR)
    out = src / "viz"
    out.mkdir(exist_ok=True)
    shots = sorted(src.glob("*.png"))
    shots = [s for s in shots if s.parent.name != "viz"]
    if not shots:
        print(f"在 {src} 找不到 png")
        sys.exit(1)

    backend = m.PERCEIVE_BACKENDS[BACKEND]
    print(f"{len(shots)} 张 | 后端={BACKEND} -> {out}\n")
    print(f"{'图':<16}{'棋盘':>4}{'战备':>4}  结果摘要 + 动作")
    print("-" * 92)

    for sp in shots:
        img = cv2.imdecode(np.fromfile(str(sp), dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            print(f"{sp.name}: 读取失败")
            continue
        st = backend(img)
        disp = img.copy()
        h, w = img.shape[:2]

        # 感知 overlays
        for ov in st.get("overlays", []):
            l, t, r, b = ov.get("box", (0, 0, 0, 0))
            color = ov.get("color", (0, 255, 0))
            cv2.rectangle(disp, (l, t), (r, b), color, 2)
            if ov.get("label"):
                disp = put_text_zh(disp, ov["label"], (l, max(0, t - 22)),
                                   color_bgr=color, px=18)

        # 决策动作
        actions = []
        if DRAW_ACTIONS and BACKEND == "decide":
            builder = TftActions(m._load_rois(), w, h)
            actions = m.rule_decide(st, builder)
            for a in actions:
                disp = draw_action(disp, a, w, h)

        ocr = st.get("ocr", {})
        items = st.get("items", {})
        item_str = "".join("✓" if items.get(f"item{i}") else "·" for i in range(3)) if items else ""
        shop_mark = "店开" if st.get("shop_open") else ""
        drops_n = len(st.get("drops", []) or [])
        summary = (f"棋盘{st.get('champion_count', 0)} 战备{st.get('bench_count', 0)} "
                   f"掉落{drops_n} {shop_mark} 装备[{item_str}] | "
                   f"动作{len(actions)}个")
        disp = overlay_text(disp, summary, (10, 14), color_bgr=(255, 255, 255), px=22)
        cv2.imencode(".png", disp)[1].tofile(str(out / sp.name))

        act_str = ",".join(a.description for a in actions[:6])
        print(f"{sp.name:<16}{st.get('champion_count', 0):>4}{st.get('bench_count', 0):>4}  "
              f"{shop_mark} 掉落{drops_n} 装备[{item_str}] 动作{len(actions)}: {act_str}")

    print(f"\n完成, viz 在 {out}")


if __name__ == "__main__":
    main()

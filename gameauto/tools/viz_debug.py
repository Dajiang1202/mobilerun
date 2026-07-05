#!/usr/bin/env python3
"""批量 debug 可视化 —— 对痛点截图跑全部感知层, 画出所有识别结果 + 可能决策。

分层显示 (一眼看出哪层断):
  🟩 绿框 = 棋盘棋子血条 + 点击点 + 名字(如有)
  🟧 橙框 = 战备棋子血条 + 点击点
  🔴 红圈 = 问号掉落物 (模板匹配)
  🟦 蓝框+✓/空 = 装备槽金边检测
  🟨 黄字 = OCR(gold/shop/stage/timer/店开)
  🟠 橙圆/箭 = 可能的动作(买/拖装备/走回老巢/卖)

用法: 把痛点截图放一个目录, 改顶部 SRC_DIR, 然后:
    python gameauto/tools/viz_debug.py
输出在 SRC_DIR/debug_viz/
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import gameauto.run_tft_replay as m
from gameauto.skills.tft.actions import TftActions
from gameauto.skills.tft.phase import PhaseTracker
from gameauto.tools.cv_text import overlay_multi

# ═══════════════════════════════════════════════════════════════════════
#  配置区
# ═══════════════════════════════════════════════════════════════════════

# 痛点截图目录 (把要分析的图放这里)
SRC_DIR = r"D:\gameauto\mobilerun\gameauto\core\capture\scrcpy\captured"

# 入图缩放: 截图如果是原图(scale=1), 缩成 scale=2 跟游戏帧一致
INPUT_SCALE = 2   # 1=不缩 | 2=缩一半(匹配游戏scale=2)

# ═══════════════════════════════════════════════════════════════════════

_TEMPLATES_DIR = (Path(__file__).resolve().parent.parent
                  / "skills" / "tft" / "assets" / "templates")
_DROP_NAMES = ["drop_blue", "drop_white", "drop_gold"]


def _detect_drops_sync(frame, tm, rois, fw, fh, threshold=0.80):
    """同步版 drops 检测 (viz 工具不是 async)。"""
    if tm is None:
        return []
    names = [n for n in _DROP_NAMES if n in tm.template_names]
    if not names:
        return []
    from gameauto.run_tft_replay import _flat_roi
    region = _flat_roi(rois, "ocr", "drop_region") or (0, 0, 1, 1)
    L, T = int(region[0]*fw), int(region[1]*fh)
    R, B = int(region[2]*fw), int(region[3]*fh)
    crop = frame[T:B, L:R]
    if crop.size == 0:
        return []
    drops = []
    for name in names:
        tpl = tm._templates.get(name)
        if tpl is None:
            continue
        for scale in (0.9, 1.0, 1.1):
            sw, sh = max(1, int(tpl.shape[1]*scale)), max(1, int(tpl.shape[0]*scale))
            if sw > crop.shape[1] or sh > crop.shape[0]:
                continue
            scaled = cv2.resize(tpl, (sw, sh))
            res = cv2.matchTemplate(crop, scaled, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(res >= threshold)
            for x, y in zip(xs, ys):
                cx, cy = x + sw//2 + L, y + sh//2 + T
                # 去重 (附近已有)
                if all(abs(cx-dx)+abs(cy-dy) > 30 for dx, dy in drops):
                    drops.append((cx, cy))
    return drops


def main():
    src = Path(SRC_DIR)
    out = src / "debug_viz"
    out.mkdir(parents=True, exist_ok=True)
    shots = sorted([f for f in src.glob("*.png") if f.parent.name != "debug_viz"])
    if not shots:
        print(f"在 {src} 找不到 png。改顶部 SRC_DIR。")
        sys.exit(1)

    rois = m._load_rois()
    tm = None
    if _TEMPLATES_DIR.is_dir():
        from gameauto.core.perception.cv.template_match import TemplateMatchTask
        tm = TemplateMatchTask(str(_TEMPLATES_DIR))
        tm.scale_templates(0.5)   # 模板 scale=1 裁的, debug 图是 scale=2 → ÷2
    has_drops = [n for n in _DROP_NAMES if tm and n in tm.template_names]

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print(f"{len(shots)} 张 | 掉落模板: {has_drops or '(无)'} → {out}\n")

    for sp in shots:
        img = cv2.imdecode(np.fromfile(str(sp), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            print(f"{sp.name}: 读取失败")
            continue
        # 入图缩放 (原图 scale=1 → scale=2, 跟游戏帧一致)
        if INPUT_SCALE > 1:
            img = cv2.resize(img, (img.shape[1] // INPUT_SCALE, img.shape[0] // INPUT_SCALE),
                             interpolation=cv2.INTER_AREA)
        fh, fw = img.shape[:2]
        disp = img.copy()
        builder = TftActions(rois, fw, fh)

        # ── 1. 全量感知 ──
        st = m.decide_perceive(img)
        ocr = st.get("ocr", {})
        board = st.get("board_clicks", []) or []
        bench = st.get("bench_clicks", []) or []
        board_bars = st.get("board_bars", []) or []
        bench_bars = st.get("bench_bars", []) or []

        # ── 2. 问号掉落物 (模板匹配) ──
        drops = _detect_drops_sync(img, tm, rois, fw, fh)

        # ── 3. 装备槽 (金边) ──
        items, item_ov, _ = m.detect_items(img, rois)

        # ── 4. 阶段 ──
        import re
        stage_raw = ocr.get("stage", "")
        sm = re.search(r"\d+\s*[-\-–—]\s*\d+", stage_raw)
        stage = sm.group().replace(" ", "") if sm else None
        timer_raw = ocr.get("timer", "")
        tm_m = re.search(r"\d+", timer_raw)
        timer = int(tm_m.group()) if tm_m else None
        tracker = PhaseTracker()
        phase = tracker.update(stage, timer)

        # ── 画 ──
        items_draw = []  # overlay_multi 的 items

        # 血条框
        for bx in board_bars:
            cv2.rectangle(disp, (bx[0], bx[1]), (bx[2], bx[3]), (0, 255, 0), 2)
        for bx in bench_bars:
            cv2.rectangle(disp, (bx[0], bx[1]), (bx[2], bx[3]), (0, 200, 255), 2)
        # 棋子点击点
        for i, pos in enumerate(board):
            cv2.circle(disp, pos, 10, (0, 255, 0), 2)
            items_draw.append((f"棋{i}", (pos[0]-15, pos[1]+12), (0,255,0), 14))
        for i, pos in enumerate(bench):
            cv2.circle(disp, pos, 10, (0, 200, 255), 2)
            items_draw.append((f"备{i}", (pos[0]-15, pos[1]+12), (0,200,255), 14))

        # 问号掉落物
        for dp in drops:
            cv2.circle(disp, dp, 25, (0, 0, 255), 3)
            cv2.putText(disp, "?", (dp[0]-10, dp[1]+8), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,0,255), 2)

        # 装备槽
        for ov in item_ov:
            l, t, r, b = ov["box"]
            col = ov.get("color", (0, 255, 0))
            cv2.rectangle(disp, (l, t), (r, b), col, 2)
            items_draw.append((ov.get("label",""), (l, max(0,t-20)), col, 14))

        # ROI 框 (淡灰)
        for key in ("own_board", "bench", "drop_region", "home", "equip_btn",
                    "refresh_btn", "champion"):
            roi = m._flat_roi(rois, "ocr", key)
            if roi:
                cv2.rectangle(disp, (int(roi[0]*fw), int(roi[1]*fh)),
                              (int(roi[2]*fw), int(roi[3]*fh)), (80, 80, 80), 1)

        # 信息文字块
        items_draw.append((f"{phase} stage={stage} t={timer}", (12,10), (0,255,255), 24))
        items_draw.append((f"gold={ocr.get('gold','')} shop={[ocr.get(f'shop{i}','') or '·' for i in range(5)]}",
                           (12,40), (255,255,255), 18))
        items_draw.append((f"店开={st.get('shop_open')} 棋盘{len(board)} 战备{len(bench)} 掉落{len(drops)} 装备{items}",
                           (12,62), (255,255,255), 18))
        if board:
            items_draw.append((f"棋盘点: {board}", (12,84), (0,255,0), 14))
        if drops:
            items_draw.append((f"掉落点: {drops}", (12,102), (0,0,255), 14))

        disp = overlay_multi(disp, items_draw, bg_alpha=0.5)
        cv2.imencode(".png", disp)[1].tofile(str(out / sp.name))

        # 控制台摘要
        print(f"{sp.name}: {phase} 棋盘{len(board)} 战备{len(bench)} "
              f"掉落{len(drops)} 装备{items} 店开={st.get('shop_open')} "
              f"gold={ocr.get('gold','')}")

    print(f"\n完成, debug viz 在 {out}")


if __name__ == "__main__":
    main()

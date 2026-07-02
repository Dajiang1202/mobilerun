#!/usr/bin/env python3
"""TFT 真机入口 —— M1 观察 / M2 交互动作调试。

M1 observe: 连真机跑感知(血条+OCR+阶段), 打印识别到什么 + 状态机阶段, 不动作。
M2 act:     连真机输入命令, 随时执行中层动作(refresh/buy/click/sell/equip...) 调试。

改下面 ══ 配置区 ══, 然后:
    python run_tft_device.py

前提: OCR 服务在跑 (127.0.0.1:8089); 真机已连 (scrcpy)。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from gameauto.core.capture.scrcpy.bridge import screenshot_bgr
from gameauto.core.capture.scrcpy import ScrcpyCapture as Capture
from gameauto.core.input.scrcpy import ScrcpyInput as Input
from gameauto.core.orchestration.base import Action
from gameauto.core.perception.cv.template_match import TemplateMatchTask
from gameauto.skills.tft.actions import TftActions
from gameauto.tools.cv_text import put_text_zh
from gameauto.utils.coordinate import to_normalized

# 复用回放工作台里写好的感知后端 (full_perceive = 血条 + OCR)
from gameauto.run_tft_replay import full_perceive, _load_rois

# ═══════════════════════════════════════════════════════════════════════
#  配置区
# ═══════════════════════════════════════════════════════════════════════

DEVICE_SERIAL = "4NZ0225613000015"   # hdc list targets 查看

MODE = "observe"   # "observe" = M1 只看 | "act" = M2 交互动作

# Scrcpy
_HERE = Path(__file__).resolve().parent   # gameauto/
SDK_JAR = str(_HERE / "resource" / "hosScrcpy-1.0.15-beta.jar")
JAVA_HOME = ""
SCALE = 2
MAX_FPS = 10

# observe (M1)
SHOW = True            # 显示预览窗 (画血条/ROI 框 + 阶段)
TICK_INTERVAL = 0.5    # 每帧间隔(s)

# ═══════════════════════════════════════════════════════════════════════

# 阶段 → 状态检测模板名 (用 crop_tft_template 标注后生效)
_PHASE_TEMPLATES = [
    ("LOBBY", "lobby_play_btn"),
    ("PLANNING", "planning_timer"),
    ("COMBAT", "combat_indicator"),
    ("RESULT", "result_rank"),
    ("CAROUSEL", "carousel_banner"),
    ("AUGMENT", "augment_frame"),
    ("PVE", "pve_indicator"),
]
_TEMPLATES_DIR = _HERE / "skills" / "tft" / "assets" / "templates"


def detect_phase(png_bytes: bytes, tm: TemplateMatchTask | None) -> str:
    """用模板匹配判当前阶段。没模板返回 '未知'。"""
    if tm is None or not tm.template_names:
        return "未知(先用 crop_tft_template 标状态模板)"
    for phase, name in _PHASE_TEMPLATES:
        if name in tm.template_names and tm._match_inline(png_bytes, (0, 0, 1, 1), name, 0.72):
            return phase
    return "UNKNOWN"


async def _execute(action: Action, inp: Input) -> None:
    """执行单个 Action (归一化[0-1000]) → ScrcpyInput。"""
    if action.type == "tap":
        await inp.tap(action.x1, action.y1, 150)
    elif action.type in ("swipe", "drag"):
        await inp.swipe(action.x1, action.y1, action.x2, action.y2, action.duration_ms)
    elif action.type == "wait":
        await asyncio.sleep(action.duration_ms / 1000.0)


# ── M1: observe ────────────────────────────────────────────────────────

async def observe(capture: Capture, tm: TemplateMatchTask | None) -> None:
    print("=== M1 观察: 跑感知, 打印识别+阶段, 不动作 (按 q 退出) ===")
    if SHOW:
        cv2.namedWindow("TFT device observe", cv2.WINDOW_NORMAL)
    while True:
        frame = screenshot_bgr()
        if frame is None:
            await asyncio.sleep(0.05)
            continue
        ok, buf = cv2.imencode(".png", frame)
        png = buf.tobytes() if ok else b""

        state = full_perceive(frame)
        phase = detect_phase(png, tm)

        # ── 打印识别到什么 + 阶段 ──
        print(f"\n[阶段] {phase}")
        print(f"  棋盘棋子: {state.get('champion_count', 0)}  战备: {state.get('bench_count', 0)}")
        ocr = state.get("ocr", {})
        if ocr:
            print("  OCR: " + "  ".join(f"{k}={v!r}" for k, v in ocr.items() if v))
        for d in state.get("details", []):
            print("   ", d)

        # ── 预览: 画 overlays + 阶段 ──
        if SHOW:
            disp = frame.copy()
            for ov in state.get("overlays", []):
                l, t, r, b = ov.get("box", (0, 0, 0, 0))
                cv2.rectangle(disp, (l, t), (r, b), (0, 255, 0), 2)
                if ov.get("label"):
                    disp = put_text_zh(disp, ov["label"], (l, max(0, t - 24)),
                                       color_bgr=(0, 255, 0), px=20)
            disp = put_text_zh(disp, f"阶段: {phase}", (20, 20),
                               color_bgr=(0, 255, 255), px=30)
            cv2.imshow("TFT device observe", disp)
            if (cv2.waitKey(1) & 0xFF) == ord("q"):
                break

        await asyncio.sleep(TICK_INTERVAL)
    if SHOW:
        cv2.destroyAllWindows()


# ── M2: act (交互命令行) ───────────────────────────────────────────────

_HELP = """\
命令 (坐标均为像素, 自己看预览/截图量):
  refresh              刷新商店
  buy_xp               购买经验
  buy <0-4>            买商店第 N 格
  click <x> <y>        点击棋子 (弹面板)
  sell <x> <y>         出售棋子 (长按1s+拖到底)
  equip <slot> <x> <y> 从装备槽 slot(0-2) 拖到棋子(x,y)
  tap <x> <y>          裸点击 (像素)
  swipe <x1> <y1> <x2> <y2> [ms]   裸滑动
  help                 帮助
  q                    退出"""


async def act(capture: Capture, inp: Input) -> None:
    w, h = capture.native_resolution
    builder = TftActions(_load_rois(), w, h)
    print(f"=== M2 动作调试: 真机 {w}x{h} | 输入命令执行 (help 看列表, q 退出) ===")
    print(_HELP)
    loop = asyncio.get_event_loop()
    while True:
        try:
            cmd = (await loop.run_in_executor(None, input, "\n> ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not cmd or cmd == "q":
            break
        actions = _parse_cmd(cmd, builder, w, h)
        if actions is None:
            print("  未知命令, 输入 help")
            continue
        if actions == "help":
            print(_HELP)
            continue
        if not actions:
            print("  (无动作: ROI 没标或参数不对)")
            continue
        for a in actions:
            print(f"  执行: {a.type} {a.description}")
            await _execute(a, inp)


def _parse_cmd(cmd: str, builder: TftActions, w: int, h: int):
    """解析命令 → list[Action]。help 返回哨兵 'help'; 未知返回 None。"""
    parts = cmd.split()
    if not parts:
        return []
    c = parts[0]
    if c == "help":
        return "help"

    def px(i):
        return int(parts[i])

    try:
        if c == "refresh":
            return builder.refresh()
        if c == "buy_xp":
            return builder.buy_xp()
        if c == "buy":
            return builder.buy_shop_slot(px(1))
        if c == "click":
            return builder.click_champion((px(1), px(2)))
        if c == "sell":
            return builder.sell_champion((px(1), px(2)))
        if c == "equip":
            return builder.equip_from_slot(px(1), (px(2), px(3)))
        if c == "tap":
            nx, ny = to_normalized(px(1), px(2), w, h)
            return [Action(type="tap", x1=nx, y1=ny, description="裸点击")]
        if c == "swipe":
            x1, y1 = to_normalized(px(1), px(2), w, h)
            x2, y2 = to_normalized(px(3), px(4), w, h)
            ms = int(parts[5]) if len(parts) > 5 else 400
            return [Action(type="drag", x1=x1, y1=y1, x2=x2, y2=y2,
                           duration_ms=ms, description="裸滑动")]
    except (IndexError, ValueError):
        return []
    return None


# ═══════════════════════════════════════════════════════════════════════

async def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

    capture = Capture(DEVICE_SERIAL, SDK_JAR, JAVA_HOME, scale=SCALE, max_fps=MAX_FPS)
    await capture.connect()
    inp = Input(DEVICE_SERIAL, SDK_JAR, JAVA_HOME, scale=SCALE, max_fps=MAX_FPS)
    await inp.connect()
    w, h = capture.native_resolution
    inp.set_input_resolution(w, h)
    print(f"设备: {DEVICE_SERIAL}  {w}x{h}  scale={SCALE}")

    tm = None
    if _TEMPLATES_DIR.is_dir():
        tm = TemplateMatchTask(str(_TEMPLATES_DIR))
        print(f"状态模板: {tm.template_names or '(空, 阶段判为未知)'}")

    try:
        if MODE == "observe":
            await observe(capture, tm)
        elif MODE == "act":
            await act(capture, inp)
        else:
            print(f"未知 MODE={MODE}, 可选 observe / act")
    except KeyboardInterrupt:
        print("\n中断")
    finally:
        await capture.disconnect()
        sys.stdout.flush()
        os._exit(0)  # JVM 残留, 强退 (同 run_tft_scrcpy)


if __name__ == "__main__":
    asyncio.run(main())

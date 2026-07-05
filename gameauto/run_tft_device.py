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
import re
import sys
import time
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
from gameauto.skills.tft.phase import PhaseTracker
from gameauto.skills.tft.pregame import PreGameDriver, make_tap_fn, ocr_full, START_KEYWORDS
from gameauto.tools.cv_text import put_text_zh, overlay_text
from gameauto.utils.coordinate import to_normalized

# 复用回放工作台里写好的感知后端 (full_perceive = 血条 + OCR)
from gameauto.run_tft_replay import (
    full_perceive, rule_decide, _load_rois, _flat_roi, _ocr_image,
)

# ═══════════════════════════════════════════════════════════════════════
#  配置区
# ═══════════════════════════════════════════════════════════════════════

DEVICE_SERIAL = "4NZ0225613000015"   # hdc list targets 查看

MODE = "auto"   # "observe"=M1只看 | "act"=M2交互 | "match"=匹配进游戏 | "auto"=自动打一局

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
            from gameauto.tools.cv_text import overlay_text
            disp = overlay_text(disp, f"阶段: {phase}", (20, 20),
                                color_bgr=(0, 255, 255), px=30)
            cv2.imshow("TFT device observe", disp)
            if (cv2.waitKey(1) & 0xFF) == ord("q"):
                break

        await asyncio.sleep(TICK_INTERVAL)
    if SHOW:
        cv2.destroyAllWindows()


# ── M0: match (预游戏自动匹配进游戏) ───────────────────────────────────

async def match(capture: Capture, inp: Input) -> None:
    """驱动 LOBBY→MATCHING→ACCEPTED_WAIT→IN_GAME, 全图 OCR 找按钮并点击。

    假设启动时已在房间主界面 (能看到「开始游戏」)。进入游戏后退出,
    后续交棒给 PLANNING (M3 接自动循环)。
    """
    frame = screenshot_bgr()
    if frame is None:
        print("[match] 拿不到画面, 退出")
        return
    h, w = frame.shape[:2]
    print(f"=== M0 预游戏匹配: 真机 {w}x{h} | OCR 全图驱动 (按 Ctrl+C 中断) ===")

    def grab_png() -> bytes:
        f = screenshot_bgr()
        if f is None:
            return b""
        ok, buf = cv2.imencode(".png", f)
        return buf.tobytes() if ok else b""

    tap_fn = make_tap_fn(inp)
    drv = PreGameDriver(tap_fn, log=lambda msg, *a: print(msg))
    await drv.run(grab_png, frame_wh=(w, h))
    print("[match] 已进入游戏。切 MODE=observe 可看感知, 或重跑继续。")


# ── M2: act (交互命令行) ───────────────────────────────────────────────

_HELP = """\
命令 (坐标均为像素, 自己看预览/截图量):
  refresh              刷新商店
  buy_xp               购买经验
  buy <0-4>            买商店第 N 格
  click <x> <y>        点击棋子 (弹面板)
  sell <x> <y>         出售棋子 (长按1s+拖到底)
  equip <slot> <x> <y> 从装备槽 slot(0-2) 拖到棋子(x,y)
  close                关闭棋子面板
  carousel             选秀拾取
  augment [0-2]        海克斯选第N个(默认0)
  continue             结算/继续
  shop                 开关商店
  drop <x> <y>         拾取掉落物(像素)
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
        if c == "close":
            return builder.close_panel()
        if c == "carousel":
            return builder.pick_carousel()
        if c == "augment":
            return builder.pick_augment(int(parts[1]) if len(parts) > 1 else 0)
        if c == "continue":
            return builder.tap_continue()
        if c == "shop":
            return builder.toggle_shop()
        if c == "drop":
            return builder.click_drop((px(1), px(2)))
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


# ── M3: auto (自动打一局) ───────────────────────────────────────────────


def _ocr_stage_timer(frame, rois, w, h):
    """轻量: 只 OCR stage + timer 两个小 ROI → (stage_str|None, timer_int|None)。

    stage 用正则提取干净的 X-Y (去掉 OCR 噪声如 '2-1 可'), 防误判 stage 变化。
    """
    timer_int = None
    sroi = _flat_roi(rois, "ocr", "stage")
    if sroi:
        L, T, R, B = int(sroi[0]*w), int(sroi[1]*h), int(sroi[2]*w), int(sroi[3]*h)
        raw = _ocr_image(frame[T:B, L:R])[0]
        m = re.search(r"\d+\s*[-\-–—]\s*\d+", raw)
        stage_txt = m.group().replace(" ", "").replace("–", "-").replace("—", "-") if m else ""
    else:
        stage_txt = ""
    troi = _flat_roi(rois, "ocr", "timer")
    if troi:
        L, T, R, B = int(troi[0]*w), int(troi[1]*h), int(troi[2]*w), int(troi[3]*h)
        m = re.search(r"\d+", _ocr_image(frame[T:B, L:R])[0])
        timer_int = int(m.group()) if m else None
    return (stage_txt or None), timer_int


def _detect_result(frame) -> bool:
    """全图 OCR 找「第X名」→ 结算。"""
    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        return False
    res = ocr_full(buf.tobytes())
    return any(re.search(r"第.{0,3}名", h.text) for h in res.hits)


async def auto(capture: Capture, inp: Input, tm) -> None:
    """自动打一局: 大厅则先匹配 → 备战按规则动作 / 战斗等待 / 结算点继续退出。

    目标: 完成一局(哪怕最后一名)。动作随机可, 状态机+操作能跑通即可。
    """
    # ⚠ 坐标转换用「帧输出分辨率」(=截图实际像素, native/scale), 不是 native。
    # 因为 OCR/感知的 box 都是帧像素; 输入侧 ScrcpyInput 自己把 [0-1000]→native。
    sample = screenshot_bgr()
    fh, fw = (sample.shape[:2] if sample is not None else
              (capture.native_resolution[1] // SCALE, capture.native_resolution[0] // SCALE))
    rois = _load_rois()
    builder = TftActions(rois, fw, fh)   # 用帧输出尺寸, pixel→[0-1000] 才对
    tracker = PhaseTracker()
    print(f"=== auto: 自动对局 | 帧 {fw}x{fh} (native {capture.native_resolution[0]}x{capture.native_resolution[1]}) ===")

    if SHOW:
        cv2.namedWindow("TFT auto", cv2.WINDOW_NORMAL)

    def grab_png(label: str = "") -> bytes:
        """截图 + 可选显示, 返回 PNG bytes。pregame 和主循环共用, 全程有画面。"""
        f = screenshot_bgr()
        if f is None:
            return b""
        if SHOW:
            disp = overlay_text(f.copy(), label or "auto", (20, 20),
                                color_bgr=(0, 255, 255), px=30)
            cv2.imshow("TFT auto", disp)
            cv2.waitKey(1)
        ok, buf = cv2.imencode(".png", f)
        return buf.tobytes() if ok else b""

    # 0) 大厅 → 先匹配进游戏 (frame_wh 用帧输出尺寸)
    png0 = grab_png("检测大厅...")
    if png0 and ocr_full(png0).find(START_KEYWORDS):
        print("[auto] 在大厅, 先跑匹配")
        await PreGameDriver(make_tap_fn(inp), log=lambda m, *a: print(m)).run(
            lambda: grab_png("匹配中..."), frame_wh=(fw, fh))

    # 1) 游戏内循环
    last_result_check = 0.0
    while True:
        frame = screenshot_bgr()
        if frame is None:
            await asyncio.sleep(0.5)
            continue

        stage, timer = _ocr_stage_timer(frame, rois, fw, fh)
        phase = tracker.update(stage, timer)

        # 结算检测 (节流: 每 5s 一次全图 OCR)
        now = time.time()
        if now - last_result_check > 5:
            last_result_check = now
            if _detect_result(frame):
                phase = "结算"

        print(f"[{phase}] stage={stage} timer={timer} ord={tracker.ordinal} "
              f"acted={tracker.acted_this_planning}")

        if phase == "备战" and not tracker.acted_this_planning:
            st = full_perceive(frame)
            actions = rule_decide(st, builder)
            print(f"  备战产出 {len(actions)} 个动作, 执行前 6 个")
            for a in actions[:6]:
                try:
                    print(f"    -> {a.type} {a.description}")
                    await _execute(a, inp)
                    await asyncio.sleep(0.6)   # 等动画
                except Exception as e:
                    print(f"    动作失败: {e}")
            tracker.mark_acted()
        elif phase == "结算":
            cont = builder.tap_continue()
            if cont:
                await _execute(cont[0], inp)
            print("[auto] 结算 → 点继续 → 结束本局")
            break
        else:
            await asyncio.sleep(1.0)

        if SHOW:
            disp = frame.copy()
            disp = overlay_text(disp, f"{phase}  stage={stage} t={timer}",
                                (20, 20), color_bgr=(0, 255, 255), px=30)
            cv2.imshow("TFT auto", disp)
            if (cv2.waitKey(1) & 0xFF) == ord("q"):
                break
        await asyncio.sleep(0.3)
    if SHOW:
        cv2.destroyAllWindows()


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
        elif MODE == "match":
            await match(capture, inp)
        elif MODE == "auto":
            await auto(capture, inp, tm)
        else:
            print(f"未知 MODE={MODE}, 可选 observe / act / match / auto")
    except KeyboardInterrupt:
        print("\n中断")
    finally:
        await capture.disconnect()
        sys.stdout.flush()
        os._exit(0)  # JVM 残留, 强退 (同 run_tft_scrcpy)


if __name__ == "__main__":
    asyncio.run(main())

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
import random
import re
import sys
import threading
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
from gameauto.skills.tft.pregame import (
    PreGameDriver, make_tap_fn, ocr_full, START_KEYWORDS, hit_to_1000,
)
from gameauto.tools.cv_text import put_text_zh, overlay_text, overlay_multi
from gameauto.utils.coordinate import to_normalized

# 复用回放工作台里写好的感知后端 (full_perceive = 血条 + OCR)
from gameauto.run_tft_replay import (
    full_perceive, decide_perceive, detect_items,
    _load_rois, _flat_roi, _ocr_image,
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
SHOW = True            # 显示预览窗 (OCR结果/棋子名/决策/血条)
SHOW_ROIS = False      # 预览窗是否画 ROI 框 (调试用, 默认关)
TICK_INTERVAL = 0.5    # 每帧间隔(s)

# 截图/日志保存
SAVE_DIR = str(_HERE / "logs")   # 截图/识别结果/调试日志存这
DEBUG_CLICK = True              # 开: 每次点击前后存原图+识别结果, 打意图log
_shot_counter = 0

def save_screenshot(frame, tag: str = "") -> str:
    """保存截图到 SAVE_DIR/screenshots/, 返回路径。"""
    global _shot_counter
    _shot_counter += 1
    d = Path(SAVE_DIR) / "screenshots"
    d.mkdir(parents=True, exist_ok=True)
    name = f"shot_{_shot_counter:04d}" + (f"_{tag}" if tag else "") + ".png"
    p = d / name
    cv2.imencode(".png", frame)[1].tofile(str(p))
    print(f"  📷 已保存 {p}")
    return str(p)


def _debug_click_tag(intent: str, inp_obj=None):
    """DEBUG_CLICK=True 时, 返回一个上下文管理器:
    进: 截图原图 + 全量感知 + 存盘; 出: 截图结果图 + 存盘。
    intent: 点击意图描述 (如 '买商店2', '点棋盘0', '开装备栏')。
    """
    class _Ctx:
        def __init__(self):
            self.intent = intent
            self.dir = Path(SAVE_DIR) / "click_debug"
            self.dir.mkdir(parents=True, exist_ok=True)
            self._n = 0
        def __enter__(self):
            if not DEBUG_CLICK:
                return self
            self._n = _shot_counter
            before = screenshot_bgr()
            if before is not None:
                print(f"  🔍 [点击调试] {self.intent} ← 截图前")
                cv2.imencode(".png", before)[1].tofile(str(self.dir / f"{self._n:04d}_{intent}_before.png"))
            return self
        def __exit__(self, *exc):
            if not DEBUG_CLICK:
                return
            after = screenshot_bgr()
            if after is not None:
                print(f"  🔍 [点击调试] {self.intent} → 截图后")
                cv2.imencode(".png", after)[1].tofile(str(self.dir / f"{self._n:04d}_{intent}_after.png"))
    return _Ctx()

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


_click_seq = 0

async def _execute(action: Action, inp: Input) -> None:
    """执行单个 Action (归一化[0-1000]) → ScrcpyInput。

    DEBUG_CLICK=True 时, 每次点击前后截图存盘 + 打意图。
    """
    global _click_seq
    is_click = action.type in ("tap", "swipe", "drag")
    if is_click and DEBUG_CLICK:
        _click_seq += 1
        n = _click_seq
        d = Path(SAVE_DIR) / "click_debug"
        d.mkdir(parents=True, exist_ok=True)
        tag = action.description or action.type
        # 前
        before = screenshot_bgr()
        if before is not None:
            cv2.imencode(".png", before)[1].tofile(str(d / f"{n:04d}_{tag}_before.png"))
        # 坐标换算到帧像素 (用于 log)
        from gameauto.utils.coordinate import to_absolute
        fh_s, fw_s = (before.shape[:2] if before is not None else (0, 0))
        if action.type == "tap":
            px, py = to_absolute(action.x1, action.y1, fw_s, fh_s) if fw_s else (0, 0)
            print(f"  🔍 [#{n}] TAP {tag} norm=({action.x1},{action.y1}) px=({px},{py})")
            await inp.tap(action.x1, action.y1, 150)
        elif action.type in ("swipe", "drag"):
            p1 = to_absolute(action.x1, action.y1, fw_s, fh_s) if fw_s else (0, 0)
            p2 = to_absolute(action.x2, action.y2, fw_s, fh_s) if fw_s else (0, 0)
            print(f"  🔍 [#{n}] DRAG {tag} {p1}→{p2} {action.duration_ms}ms")
            await inp.swipe(action.x1, action.y1, action.x2, action.y2, action.duration_ms)
        # 后
        await asyncio.sleep(0.3)
        after = screenshot_bgr()
        if after is not None:
            cv2.imencode(".png", after)[1].tofile(str(d / f"{n:04d}_{tag}_after.png"))
    else:
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
  ── 动作 ──
  refresh / buy_xp / buy <0-4> / shop
  click <x> <y> / sell <x> <y> / close
  equip <slot> <x> <y>      从装备槽拖到棋子
  equip_xy <fx fy tx ty>    拖装备(任意位置)到棋子(任意位置)
  drop <x> <y> / tap <x y> / swipe <x1 y1 x2 y2> [ms]
  carousel / augment [0-2] / continue
  ── 子流程调试 ──
  iterate        遍历所有棋子(点开→OCR名→关→汇总)
  perceive       一次性感知(gold/shop/血条/装备/掉落)
  drops          模板匹配检测问号掉落物
  equip_open     点装备栏按钮(展开)
  equip_check    检测装备槽金边(有无装备)
  home           拖一个棋子走回老巢
  ── 其他 ──
  help           帮助 | q 退出
  预览窗: 按 s 存截图"""


async def act(capture: Capture, inp: Input) -> None:
    w, h = capture.native_resolution
    rois = _load_rois()
    f0 = screenshot_bgr()
    fh, fw = f0.shape[:2] if f0 is not None else (h // SCALE, w // SCALE)
    builder = TftActions(rois, fw, fh)
    # 模板 (drops 检测用) — 模板在 scale=1 裁的, 游戏跑 scale=2 要÷SCALE
    tm = None
    if _TEMPLATES_DIR.is_dir():
        tm = TemplateMatchTask(str(_TEMPLATES_DIR))
        if SCALE > 1:
            tm.scale_templates(1.0 / SCALE)
    print(f"=== M2 动作调试: 帧 {fw}x{fh} | help 看列表, q 退出, 预览窗按 s 存截图 ===")

    # 预览线程 (daemon): 实时显示画面 + 's' 存截图
    _stop = threading.Event()
    def _preview():
        cv2.namedWindow("TFT act", cv2.WINDOW_NORMAL)
        while not _stop.is_set():
            frame = screenshot_bgr()
            if frame is not None:
                cv2.imshow("TFT act", frame)
                key = cv2.waitKey(80) & 0xFF
                if key == ord("s"):
                    save_screenshot(frame, "manual")
            else:
                time.sleep(0.05)
        cv2.destroyAllWindows()
    threading.Thread(target=_preview, daemon=True).start()

    loop = asyncio.get_event_loop()
    try:
        while True:
            try:
                cmd = (await loop.run_in_executor(None, input, "\n> ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not cmd or cmd == "q":
                break

            # ── 子流程命令 (async, 在此直接处理) ──
            if cmd == "iterate":
                await _cmd_iterate(builder, inp, rois, fw, fh)
                continue
            if cmd == "perceive":
                _cmd_perceive(fw, fh)
                continue
            if cmd == "drops":
                frame = screenshot_bgr()
                if frame is not None:
                    drops = await detect_drops_tm(frame, tm, rois, fw, fh)
                    print(f"  检出 {len(drops)} 个掉落物: {drops}")
                    if drops:
                        disp = frame.copy()
                        for dp in drops:
                            cv2.circle(disp, dp, 20, (0, 0, 255), 3)
                        save_screenshot(disp, "drops")
                continue
            if cmd == "equip_open":
                roi = _flat_roi(rois, "ocr", "equip_btn")
                if roi:
                    ex, ey = builder._roi_mid1000(roi)
                    print(f"  点装备栏 ({ex},{ey})")
                    await inp.tap(ex, ey, 150)
                else:
                    print("  equip_btn ROI 没标")
                continue
            if cmd == "equip_check":
                frame = screenshot_bgr()
                if frame is not None:
                    items, _, det = detect_items(frame, rois)
                    for d in det:
                        print(f"  {d}")
                continue
            if cmd == "home":
                frame = screenshot_bgr()
                if frame is not None:
                    st = decide_perceive(frame)
                    await _tap_home(inp, rois, fw, fh)
                continue

            # ── 常规动作命令 ──
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
            # 命令后截图
            frame = screenshot_bgr()
            if frame is not None:
                save_screenshot(frame, cmd.replace(" ", "_")[:20])
    finally:
        _stop.set()


async def _cmd_iterate(builder: TftActions, inp: Input, rois, fw, fh) -> None:
    """遍历所有棋子: 点开→OCR名→关→汇总 + 保存识别裁图。"""
    frame = screenshot_bgr()
    if frame is None:
        return
    st = decide_perceive(frame)
    board = st.get("board_clicks", []) or []
    bench = st.get("bench_clicks", []) or []
    print(f"  检出: 棋盘{len(board)} 战备{len(bench)}")
    champion_roi = _flat_roi(rois, "ocr", "champion")
    collected = {"棋盘": [], "战备": []}
    for label, clicks in (("棋盘", board), ("战备", bench)):
        for i, pos in enumerate(clicks):
            print(f"  [点击] {label}{i}/{len(clicks)} @ {pos}")
            for a in builder.click_champion(pos):
                await _execute(a, inp)
            await asyncio.sleep(0.5)
            name = ""
            if champion_roi:
                L, T, R, B = (int(champion_roi[0]*fw), int(champion_roi[1]*fh),
                              int(champion_roi[2]*fw), int(champion_roi[3]*fh))
                f3 = screenshot_bgr()
                if f3 is not None:
                    crop = f3[T:B, L:R].copy()
                    name = _ocr_image(crop)[0].strip()
                    print(f"    识别: {name!r}")
                    d = Path(SAVE_DIR) / "champions"
                    d.mkdir(parents=True, exist_ok=True)
                    safe = name.replace("/", "_") or "unknown"
                    cv2.imencode(".png", crop)[1].tofile(str(d / f"{label}{i}_{safe}.png"))
            collected[label].append(name or "?")
            for a in builder.close_panel():
                await _execute(a, inp)
            await asyncio.sleep(0.4)
    print(f"  === 汇总 ===")
    print(f"  上场({len(collected['棋盘'])}): {collected['棋盘']}")
    print(f"  场下({len(collected['战备'])}): {collected['战备']}")


def _cmd_perceive(fw, fh) -> None:
    """一次性全量感知, 打印 gold/shop/血条/装备/掉落。"""
    frame = screenshot_bgr()
    if frame is None:
        return
    st = decide_perceive(frame)
    ocr = st.get("ocr", {})
    print(f"  gold={ocr.get('gold','')} stage={ocr.get('stage','')} level={ocr.get('level','')}")
    print(f"  shop={[ocr.get(f'shop{i}','') or '·' for i in range(5)]}")
    print(f"  店开={st.get('shop_open')} 血条=棋盘{len(st.get('board_clicks',[]) or [])}"
          f"+战备{len(st.get('bench_clicks',[]) or [])} 装备={st.get('items')}")


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
        if c == "equip_xy":
            # 拖装备(任意位置)到棋子(任意位置): equip_xy <fx> <fy> <tx> <ty>
            fx, fy = to_normalized(px(1), px(2), w, h)
            tx, ty = to_normalized(px(3), px(4), w, h)
            return [Action(type="drag", x1=fx, y1=fy, x2=tx, y2=ty, duration_ms=400,
                           description=f"拖装备({px(1)},{px(2)})→棋子({px(3)},{px(4)})")]
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
    """全图 OCR 找「第X名」→ 结算。(保留备用, auto 主循环已合并到节流全图 OCR)"""
    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        return False
    res = ocr_full(buf.tobytes())
    return any(re.search(r"第.{0,3}名", h.text) for h in res.hits)


def _shop_open(frame, rois, fw, fh) -> bool:
    """refresh_btn 区有「刷新」= 商店开着。"""
    rroi = _flat_roi(rois, "ocr", "refresh_btn")
    if not rroi:
        return False
    L, T, R, B = int(rroi[0]*fw), int(rroi[1]*fh), int(rroi[2]*fw), int(rroi[3]*fh)
    return "刷新" in _ocr_image(frame[T:B, L:R])[0]


def _shop_visible(frame, rois, fw, fh) -> bool:
    """商店可见 = refresh 区有「刷新」或 buy_xp 区有「经验」(死亡/观战时看不到这俩)。"""
    for key, kw in [("refresh_btn", "刷新"), ("buy_xp_btn", "经验")]:
        roi = _flat_roi(rois, "ocr", key)
        if not roi:
            continue
        L, T, R, B = int(roi[0]*fw), int(roi[1]*fh), int(roi[2]*fw), int(roi[3]*fh)
        if kw in _ocr_image(frame[T:B, L:R])[0]:
            return True
    return False


# 掉落物模板匹配阈值 (用户要求不要太高) + 走回老巢触发倒计时
DROP_TM_THRESHOLD = 0.80
WALK_HOME_TIMER = 3
_DROP_NAMES = ["drop_blue", "drop_white", "drop_gold"]


async def detect_drops_tm(frame, tm, rois, fw, fh, threshold: float = DROP_TM_THRESHOLD):
    """模板匹配 3 种问号掉落物(蓝/白/金), 在 drop_region 内搜, 返回 [(cx,cy),...] 像素。"""
    if tm is None:
        return []
    names = [n for n in _DROP_NAMES if n in tm.template_names]
    if not names:
        return []
    region = _flat_roi(rois, "ocr", "drop_region") or (0.0, 0.0, 1.0, 1.0)
    L, T, R, B = int(region[0]*fw), int(region[1]*fh), int(region[2]*fw), int(region[3]*fh)
    crop = frame[T:B, L:R]
    if crop.size == 0:
        return []
    ok, buf = cv2.imencode(".png", crop)
    if not ok:
        return []
    res = await tm.run(buf.tobytes(), roi=None,
                       config={"threshold": threshold, "filter_names": names})
    drops = []
    for m in res.get("matches", []):
        cx = m["x"] + m["w"] // 2 + L
        cy = m["y"] + m["h"] // 2 + T
        drops.append((cx, cy))
    return drops


async def _tap_home(inp: Input, rois, fw, fh) -> None:
    """点击老巢位置(home ROI 中心)。"""
    home = _flat_roi(rois, "ocr", "home")
    if not home:
        return
    hx, hy = int((home[0] + home[2]) / 2 * fw), int((home[1] + home[3]) / 2 * fh)
    nx, ny = to_normalized(hx, hy, fw, fh)
    print(f"  点老巢 ({hx},{hy})")
    await inp.tap(nx, ny, 150)


def _build_situation(board_names, bench_names, ocr):
    """构建自然语言局势描述, 供后续 LLM 决策用。

    返回类似: "场上: 凯尔(1费,暗星/牧羊人), 波比(1费) | 战备: 瑟提(2费) |
    激活: 暗星2档 牧羊人1档 | 金币42 等级6 | 商店: [凯尔,波比,·,·,·]"
    """
    try:
        from gameauto.skills.tft.data import champion_info, compute_active_traits
    except ImportError:
        return "(data.py 不可用)"

    def _fmt(names):
        parts = []
        for n in names:
            if not n or n == "?":
                continue
            info = champion_info(n)
            if info:
                traits = "/".join(info.get("traits", [])[:3])
                parts.append(f"{n}({info.get('cost','?')}费,{traits})" if traits else f"{n}({info.get('cost','?')}费)")
            else:
                parts.append(f"{n}(未识别)")
        return parts

    board_fmt = _fmt(board_names)
    bench_fmt = _fmt(bench_names)
    all_real = [n for n in board_names + bench_names if n and n != "?" and champion_info(n)]
    traits = compute_active_traits(all_real) if all_real else []

    parts = []
    if board_fmt:
        parts.append(f"场上: {', '.join(board_fmt)}")
    if bench_fmt:
        parts.append(f"战备: {', '.join(bench_fmt)}")
    if traits:
        parts.append("激活: " + " ".join(f"{t['trait']}{t['tier']}档" for t in traits[:5]))
    parts.append(f"金币{ocr.get('gold','?')} 等级{ocr.get('level','?')}")
    shop = [ocr.get(f'shop{i}','') or '·' for i in range(5)]
    parts.append(f"商店:{shop}")
    return " | ".join(parts)


async def _do_planning(frame, st, builder: TftActions, inp: Input, rois, fw, fh):
    """备战阶段完整序列 (新时序):

    1. 30% 买经验
    2. 轮询角色 (棋盘+bench 点开→OCR名→关, 会关商店)
    3. 商店关了 → 点 gold 打开
    4. 购买 (重新OCR商店, 随机买有字的槽)
    5. 收起商店 (点 gold)
    6. 上装备 (开装备栏→金边检测→拖给场上棋子→关)
    7. 卖 (战备>5)
    8. 构建自然语言局势描述 (供 LLM)
    问号留战斗阶段。
    返回 (names_map, situation_desc)。
    """
    board = st.get("board_clicks", []) or []
    bench = st.get("bench_clicks", []) or []
    ocr = st.get("ocr", {})
    champion_roi = _flat_roi(rois, "ocr", "champion")

    # 1) 30% 买经验
    if random.random() < 0.3:
        print("  [30%] 买经验")
        for a in builder.buy_xp():
            await _execute(a, inp)
        await asyncio.sleep(0.5)

    # 1.5) 先收起商店(如果开着), 等1s让棋盘完全可见, 再轮询
    f_pre = screenshot_bgr()
    if f_pre is not None and _shop_open(f_pre, rois, fw, fh):
        print("  先收起商店, 等1s再轮询")
        for a in builder.toggle_shop():
            await _execute(a, inp)
        await asyncio.sleep(1.0)

    # 2) 轮询角色 (棋盘+战备, 会关商店)
    total = len(board) + len(bench)
    print(f"  [轮询] 共 {total} 个棋子 (棋盘{len(board)} + 战备{len(bench)})")
    collected = {"棋盘": [], "战备": []}
    names_map: dict = {}
    for label, clicks in (("棋盘", board), ("战备", bench)):
        for i, pos in enumerate(clicks):
            print(f"  [点击] {label}{i}/{len(clicks)} @ {pos}")
            for a in builder.click_champion(pos):
                await _execute(a, inp)
            await asyncio.sleep(0.5)
            name = ""
            if champion_roi:
                L, T, R, B = (int(champion_roi[0]*fw), int(champion_roi[1]*fh),
                              int(champion_roi[2]*fw), int(champion_roi[3]*fh))
                f3 = screenshot_bgr()
                if f3 is not None:
                    crop = f3[T:B, L:R].copy()
                    name = _ocr_image(crop)[0].strip()
                    print(f"    识别: {name!r}")
                    save_dir = Path(SAVE_DIR) / "champions"
                    save_dir.mkdir(parents=True, exist_ok=True)
                    safe = name.replace("/", "_").replace("\\", "_") or "unknown"
                    cv2.imencode(".png", crop)[1].tofile(str(save_dir / f"{label}{i}_{safe}.png"))
            collected[label].append(name or "?")
            names_map[pos] = name or "?"
            for a in builder.close_panel():
                await _execute(a, inp)
            await asyncio.sleep(0.4)
    print(f"  上场({len(collected['棋盘'])}): {collected['棋盘']}")
    print(f"  场下({len(collected['战备'])}): {collected['战备']}")

    # 3) 轮询关了商店 → 点 gold 打开
    f_check = screenshot_bgr()
    if f_check is not None and not _shop_open(f_check, rois, fw, fh):
        print("  轮询关了商店, 点 gold 打开")
        for a in builder.toggle_shop():
            await _execute(a, inp)
        await asyncio.sleep(0.6)

    # 4) 购买: 金币≥30 先刷新; 优先买场上已有棋子, 否则随机
    f_shop = screenshot_bgr()
    if f_shop is not None:
        # 读当前金币, ≥30 先刷新商店
        groi = _flat_roi(rois, "ocr", "gold")
        cur_gold = None
        if groi:
            L, T, R, B = int(groi[0]*fw), int(groi[1]*fh), int(groi[2]*fw), int(groi[3]*fh)
            gm = re.search(r"\d+", _ocr_image(f_shop[T:B, L:R])[0])
            cur_gold = int(gm.group()) if gm else None
        if cur_gold is not None and cur_gold >= 30:
            print(f"  金币{cur_gold}≥30, 先刷新商店")
            for a in builder.refresh():
                await _execute(a, inp)
            await asyncio.sleep(0.5)
            f_shop = screenshot_bgr()
            if f_shop is None:
                f_shop = screenshot_bgr()
        shop_texts = []
        for i in range(5):
            sroi = _flat_roi(rois, "ocr", f"shop{i}")
            if sroi:
                L, T, R, B = int(sroi[0]*fw), int(sroi[1]*fh), int(sroi[2]*fw), int(sroi[3]*fh)
                t = _ocr_image(f_shop[T:B, L:R])[0].strip()
                shop_texts.append(t)
            else:
                shop_texts.append("")
        cands = [i for i, t in enumerate(shop_texts) if t]
        if cands:
            # 优先买场上已有的棋子(凑星级); 没有再随机
            my_names = set(collected["棋盘"] + collected["战备"]) - {"?", ""}
            idx = None
            for i in cands:
                if shop_texts[i] in my_names:
                    idx = i
                    break
            if idx is None:
                # 模糊: 商店名是场上棋子名的子串(或反过来), 抗 OCR 抖动
                for i in cands:
                    st = shop_texts[i]
                    if any(st in n or n in st for n in my_names if len(n) >= 2):
                        idx = i
                        break
            if idx is None:
                idx = random.choice(cands)
            tag = "已有" if shop_texts[idx] in my_names else "随机"
            print(f"  买商店{idx} ({shop_texts[idx]!r}, {tag})")
            for a in builder.buy_shop_slot(idx):
                await _execute(a, inp)
            await asyncio.sleep(0.5)

    # 5) 收起商店 (只有确实开着才收, 不盲目点)
    f_close = screenshot_bgr()
    if f_close is not None and _shop_open(f_close, rois, fw, fh):
        print("  收起商店")
        for a in builder.toggle_shop():
            await _execute(a, inp)
        await asyncio.sleep(0.3)

    # 6) 上装备: 开装备栏 → 验证打开了(有金边) → 拖 → 关; 没打开则跳过
    equip_btn = _flat_roi(rois, "ocr", "equip_btn")
    target = board[0] if board else (bench[0] if bench else None)
    if equip_btn and target:
        ex, ey = builder._roi_mid1000(equip_btn)
        print(f"  开装备栏 ({ex},{ey})")
        await _execute(Action(type="tap", x1=ex, y1=ey, description="开装备栏"), inp)
        await asyncio.sleep(0.8)
        f2 = screenshot_bgr()
        equipped = False
        if f2 is not None:
            items, _, _ = detect_items(f2, rois)
            has_any = any(items.values())
            if not has_any:
                print("  装备栏没打开(无金边检测到), 跳过装备")
            else:
                for slot_name, present in items.items():
                    if present:
                        slot_idx = int(slot_name.replace("item", ""))
                        print(f"  装备槽{slot_idx}→棋子 @ {target}")
                        for a in builder.equip_from_slot(slot_idx, target):
                            await _execute(a, inp)
                        await asyncio.sleep(0.4)
                        equipped = True
        if equipped:
            await _execute(Action(type="tap", x1=ex, y1=ey, description="关装备栏"), inp)
            await asyncio.sleep(0.3)

    # 7) 卖 (战备>5)
    if len(bench) > 5:
        last = bench[-1]
        print(f"  战备{len(bench)}个>5, 卖最后一个 @ {last}")
        for a in builder.sell_champion(last):
            await _execute(a, inp)
        await asyncio.sleep(0.8)

    # 8) 自然语言局势描述 (供 LLM)
    desc = _build_situation(collected["棋盘"], collected["战备"], ocr)
    print(f"  [局势] {desc}")

    return names_map, desc


async def _do_spectate(inp: Input, fw: int, fh: int) -> None:
    """我方出局后观战: 先点「继续观看」, 然后每 10s 点右侧一个存活玩家(血量>0)。

    保证游戏测试时长 — 不立即退出, 持续观战到回大厅(开始游戏出现)为止。
    """
    print("[观战] 我方出局, 进入观战")
    # 1) 优先点「继续观看」直到它消失 (最多 8 次)
    for _ in range(8):
        f = screenshot_bgr()
        if f is None:
            await asyncio.sleep(1)
            continue
        ok, buf = cv2.imencode(".png", f)
        if not ok:
            continue
        res = ocr_full(buf.tobytes())
        if res.find(START_KEYWORDS):
            print("[观战] 回到大厅, 结束观战")
            return
        hit = res.find(("继续观看", "继续观"))
        if not hit:
            break
        x, y = hit_to_1000(hit, fw, fh)
        print(f"[观战] 点继续观看 ({x},{y})")
        await inp.tap(x, y, 150)
        await asyncio.sleep(2)

    # 2) 持续观战: 每 10s 点右侧一个存活玩家(纯数字血量 >0);
    #    右侧长期无数字 → 全屏 OCR 找「现在退出」→ 点它结束本局
    print("[观战] 持续观战, 每 10s 点一个右侧存活玩家 (Ctrl+C 退出)")
    last_click = 0.0
    last_number_at = time.time()
    while True:
        f = screenshot_bgr()
        if f is None:
            await asyncio.sleep(5)
            continue
        ok, buf = cv2.imencode(".png", f)
        if not ok:
            await asyncio.sleep(5)
            continue
        res = ocr_full(buf.tobytes())
        if res.find(START_KEYWORDS):
            print("[观战] 回到大厅, 结束观战")
            return
        now = time.time()
        cands = []
        for h in res.hits:
            t = h.text.strip()
            if t.isdigit():
                n = int(t)
                if 0 < n <= 100:           # 血量范围
                    cx = sum(p[0] for p in h.box) // 4
                    cy = sum(p[1] for p in h.box) // 4
                    if cx > fw * 0.85 and cy > fh * 0.1:  # 右侧15% 且 非顶部10%
                        cands.append((hit_to_1000(h, fw, fh), n))
        if cands:
            last_number_at = now
            if now - last_click >= 10:
                last_click = now
                (x, y), n = random.choice(cands)
                print(f"[观战] 点存活玩家(血{n}) ({x},{y})")
                await inp.tap(x, y, 150)
        else:
            # 右侧长期(>25s)无血量数字 → 比赛可能结束, 找「现在退出」
            if now - last_number_at > 25:
                hit = res.find(("现在退出", "现在退", "退出"))
                if hit:
                    x, y = hit_to_1000(hit, fw, fh)
                    print(f"[观战] 长期无血量, 看到「现在退出」→ 点击 ({x},{y}) 结束本局")
                    await inp.tap(x, y, 150)
                    return
        await asyncio.sleep(2)


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
    builder = TftActions(rois, fw, fh)
    tracker = PhaseTracker()

    # 会话日志: stdout 同时写到文件
    log_dir = Path(SAVE_DIR) / "auto_sessions"
    log_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    session_name = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"{session_name}.log"
    _log_file = open(log_path, "w", encoding="utf-8")
    class _Tee:
        def __init__(self, *streams): self.s = streams
        def write(self, data):
            for s in self.s: s.write(data)
        def flush(self):
            for s in self.s: s.flush()
    sys.stdout = _Tee(sys.__stdout__, _log_file)

    print(f"=== auto: 自动对局 | 帧 {fw}x{fh} (native {capture.native_resolution[0]}x{capture.native_resolution[1]}) ===")
    print(f"=== 日志: {log_path} ===")

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
    last_full_check = 0.0
    shop_closed_this_combat = False
    drops_done_this_combat = False
    equip_done_this_combat = False
    walked_home_this_combat = False
    last_board: list = []          # 最近一次备战检出的场上棋子点击点, 战斗走回老巢用
    viz_names: dict = {}           # {点击位置: 棋子名}, 预览窗画名字
    viz_st: dict = {}              # 最近一次 decide_perceive 结果, 预览窗画 OCR/血条
    prev_stage: str | None = None
    shop_seen_this_stage = False
    stages_no_shop = 0          # 连续未见商店的 stage 数; ≥2 → 判定已死亡
    while True:
        frame = screenshot_bgr()
        if frame is None:
            await asyncio.sleep(0.5)
            continue

        stage, timer = _ocr_stage_timer(frame, rois, fw, fh)
        phase = tracker.update(stage, timer)

        # 死亡检测: 备战时探商店按钮(刷新/经验); stage 变化时累计, 连续 2 个 stage
        # 都没见过商店 → 我方已死(观战中) → 进观战模式
        if phase == "备战" and _shop_visible(frame, rois, fw, fh):
            shop_seen_this_stage = True
        if stage and stage != prev_stage:
            if prev_stage is not None and not shop_seen_this_stage:
                stages_no_shop += 1
            elif shop_seen_this_stage:
                stages_no_shop = 0
            shop_seen_this_stage = False
            prev_stage = stage
            if stages_no_shop >= 2:
                print(f"[auto] 连续 {stages_no_shop} 个 stage({stage})未见商店 → 判定已死亡, 进观战")
                await _do_spectate(inp, fw, fh)
                print("[auto] 观战结束, 退出本局")
                break

        # ── 战斗阶段: 收商店 + 点击问号掉落物 + 走回老巢 ──
        if phase == "战斗":
            # 收商店 (每轮一次)
            if not shop_closed_this_combat:
                if _shop_open(frame, rois, fw, fh):
                    print("[战斗] 看见刷新, 点 gold 收起商店")
                    for a in builder.toggle_shop():
                        await _execute(a, inp)
                shop_closed_this_combat = True
            # 问号掉落物 (模板匹配; 每轮点一次) — 必须商店已收起才点
            if not drops_done_this_combat and not _shop_open(frame, rois, fw, fh):
                drops = await detect_drops_tm(frame, tm, rois, fw, fh)
                if drops:
                    print(f"[战斗] 检出 {len(drops)} 个掉落物, 逐个点击")
                    for dp in drops:
                        print(f"  点掉落物 @ {dp}")
                        for a in builder.click_drop(dp):
                            await _execute(a, inp)
                        await asyncio.sleep(1.0)   # 慢一点, 等棋子走过去拾取
                    # 点完走回老巢
                    await _tap_home(inp, rois, fw, fh)
                    drops_done_this_combat = True
            # 战斗空闲: 上装备 (开栏→检测金边→拖→关; 每轮一次)
            if not equip_done_this_combat and last_board:
                equip_btn = _flat_roi(rois, "ocr", "equip_btn")
                if equip_btn:
                    ex, ey = builder._roi_mid1000(equip_btn)
                    print(f"  [战斗] 开装备栏 ({ex},{ey})")
                    await _execute(Action(type="tap", x1=ex, y1=ey, description="开装备栏"), inp)
                    await asyncio.sleep(0.8)
                    f_eq = screenshot_bgr()
                    if f_eq is not None:
                        items, _, _ = detect_items(f_eq, rois)
                        if not any(items.values()):
                            print("  [战斗] 装备栏没打开(无金边), 跳过")
                        else:
                            for slot_name, present in items.items():
                                if present:
                                    slot_idx = int(slot_name.replace("item", ""))
                                    tgt = last_board[0]
                                    print(f"  [战斗] 装备槽{slot_idx}→棋子 @ {tgt}")
                                    for a in builder.equip_from_slot(slot_idx, tgt):
                                        await _execute(a, inp)
                                    await asyncio.sleep(0.4)
                            await _execute(Action(type="tap", x1=ex, y1=ey, description="关装备栏"), inp)
                            await asyncio.sleep(0.3)
                    equip_done_this_combat = True
            # 倒数 ≤3s: 走回老巢 (每轮一次)
            if (timer is not None and timer <= WALK_HOME_TIMER
                    and not walked_home_this_combat):
                await _tap_home(inp, rois, fw, fh)
                walked_home_this_combat = True
        else:
            # 离开战斗 → 重置 per-combat 标志
            shop_closed_this_combat = False
            drops_done_this_combat = False
            equip_done_this_combat = False
            walked_home_this_combat = False

        # 节流全图 OCR (5s): 结算 / 海克斯 / 选秀 一次查完
        now = time.time()
        if now - last_full_check > 5:
            last_full_check = now
            ok, buf = cv2.imencode(".png", frame)
            if ok:
                res = ocr_full(buf.tobytes())
                txt = res.combined_text
                if "您获得了" in txt:
                    # 我方出局 → 进入观战 (不退出, 持续看到回大厅)
                    print("[auto] 我方出局(您获得了), 进观战")
                    await _do_spectate(inp, fw, fh)
                    print("[auto] 观战结束, 退出本局")
                    break
                if re.search(r"第.{0,3}名", txt):
                    phase = "结算"
                elif any(k in txt for k in ("强化", "符文", "海克斯")):
                    idx = random.choice([0, 1, 2])
                    print(f"[海克斯] 检测到, 随机选第 {idx} 个")
                    for a in builder.pick_augment(idx):
                        await _execute(a, inp)
                    await asyncio.sleep(2.5)
                    continue
                elif "选秀" in txt or (stage and stage.endswith("-4") and not stage.startswith("1-")):
                    # 选秀: OCR 看到"选秀" 或 stage 含 -4 → 每 2s 点中心
                    # 退出条件: stage 变了(不依赖"选秀"文字, OCR 可能读不到)
                    print(f"[选秀] 检测到(stage={stage}), 每 2s 走中心")
                    for _ in range(15):
                        for a in builder.pick_carousel():
                            await _execute(a, inp)
                        await asyncio.sleep(2.0)
                        f2 = screenshot_bgr()
                        if f2 is None:
                            break
                        s2, _ = _ocr_stage_timer(f2, rois, fw, fh)
                        if s2 and s2 != stage:
                            break
                    continue

        print(f"[{phase}] stage={stage} timer={timer} ord={tracker.ordinal} "
              f"acted={tracker.acted_this_planning}")

        if phase == "备战" and not tracker.acted_this_planning:
            # ★ 先检查海克斯(2-1等回合): 有则先选, 选完再备战
            ok_aug, buf_aug = cv2.imencode(".png", frame)
            if ok_aug:
                aug_txt = ocr_full(buf_aug.tobytes()).combined_text
                if any(k in aug_txt for k in ("强化", "符文", "海克斯")):
                    idx = random.choice([0, 1, 2])
                    print(f"[备战] 先选海克斯第 {idx} 个")
                    for a in builder.pick_augment(idx):
                        await _execute(a, inp)
                    await asyncio.sleep(2.5)
                    # 选完重新拿帧, 继续备战
                    frame = screenshot_bgr()
                    if frame is None:
                        tracker.mark_acted()
                        continue
            st = decide_perceive(frame)   # full + 掉落物(?) + 商店开闭
            viz_st = st                   # 预览窗画 OCR/血条
            last_board = st.get("board_clicks", []) or []
            board_n = len(last_board)
            bench_n = len(st.get("bench_clicks", []) or [])
            print(f"  备战: 棋盘{board_n} 战备{bench_n} 店开={st.get('shop_open')} "
                  f"掉落{len(st.get('drops', []) or [])} 装备{st.get('items')}")
            viz_names, situation_desc = await _do_planning(frame, st, builder, inp, rois, fw, fh)
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

            # 血条框 (棋盘+战备)
            for bar_key, col in (("board_bars", (0, 255, 0)),
                                 ("bench_bars", (0, 200, 255))):
                for bx in viz_st.get(bar_key, []) or []:
                    cv2.rectangle(disp, (bx[0], bx[1]), (bx[2], bx[3]), col, 2)
            # 可选 ROI 框
            if SHOW_ROIS:
                for key in ("own_board", "bench", "drop_region", "home"):
                    roi = _flat_roi(rois, "ocr", key)
                    if roi:
                        cv2.rectangle(disp, (int(roi[0]*fw), int(roi[1]*fh)),
                                      (int(roi[2]*fw), int(roi[3]*fh)), (128, 128, 128), 1)

            # 批量文字 (单次 PIL, 不卡)
            ocr = viz_st.get("ocr", {}) if viz_st else {}
            items = [(f"{phase}  stage={stage}  t={timer}", (15, 12), (0,255,255), 26)]
            items.append((f"gold={ocr.get('gold','')}  shop={[ocr.get(f'shop{i}','') or '·' for i in range(5)]}",
                          (15, 44), (255,255,255), 20))
            if viz_names:
                board_names = [v for k, v in viz_names.items()
                               if k in (viz_st.get("board_clicks") or [])]
                bench_names = [v for k, v in viz_names.items()
                               if k in (viz_st.get("bench_clicks") or [])]
                items.append((f"上场({len(board_names)}): {board_names}", (15, 68), (255,255,255), 18))
                items.append((f"场下({len(bench_names)}): {bench_names}", (15, 90), (255,255,255), 18))
            # 棋子名标在位置旁
            for pos, nm in viz_names.items():
                items.append((nm, (pos[0] - 25, pos[1] + 8), (0,255,255), 16))
            disp = overlay_multi(disp, items, bg_alpha=0.55)

            # 's' 存截图
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s"):
                save_screenshot(disp, f"{phase}_{stage}")
            cv2.imshow("TFT auto", disp)
        await asyncio.sleep(0.3)
    if SHOW:
        cv2.destroyAllWindows()
    # 关闭日志
    _log_file.close()
    sys.stdout = sys.__stdout__
    print(f"日志已保存: {log_path}")


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
        if SCALE > 1:
            tm.scale_templates(1.0 / SCALE)   # 模板 scale=1 裁的, 游戏 scale=2 要÷2
        print(f"状态模板: {tm.template_names or '(空, 阶段判为未知)'} (scaled x{1.0/SCALE:.2f})")

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

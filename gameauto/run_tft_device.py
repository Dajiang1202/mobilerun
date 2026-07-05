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
from gameauto.tools.cv_text import put_text_zh, overlay_text
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
DROP_TM_THRESHOLD = 0.60
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


async def _walk_home(builder: TftActions, inp: Input, board_clicks, rois, fw, fh) -> None:
    """把一个场上棋子拖回老巢(home ROI)。board_clicks 为空则跳过。"""
    home = _flat_roi(rois, "ocr", "home")
    if not home or not board_clicks:
        return
    hx = int((home[0] + home[2]) / 2 * fw)
    hy = int((home[1] + home[3]) / 2 * fh)
    champ = board_clicks[0]
    print(f"  走回老巢: {champ} → ({hx},{hy})")
    for a in builder.move_champion(champ, (hx, hy)):
        await _execute(a, inp)


async def _do_planning(frame, st, builder: TftActions, inp: Input, rois, fw, fh) -> None:
    """备战阶段的完整动作序列 (真机, 带中途感知):

    顺序: 先买(店开着, 遍历会关商店) → 上装备(开装备栏→检测→拖) → 遍历棋子(点→OCR名→关)
          → 卖(战备>5 卖最后)。掉落物(问号)暂靠 decide_perceive drops, 识别不稳后续改模板。
    """
    board = st.get("board_clicks", []) or []
    bench = st.get("bench_clicks", []) or []
    ocr = st.get("ocr", {})

    # 注: 问号掉落物已移到战斗阶段(模板匹配), 备战不处理

    # 1) 商店购买 (店开着才买; 放遍历前, 避免遍历关了商店买不了)
    if st.get("shop_open"):
        cands = [i for i in range(5) if ocr.get(f"shop{i}")]
        if cands:
            idx = random.choice(cands)
            print(f"  买商店{idx} ({ocr.get(f'shop{idx}')!r})")
            for a in builder.buy_shop_slot(idx):
                await _execute(a, inp)
            await asyncio.sleep(0.5)

    # 2) 上装备: 点装备栏按钮 → 重新截图检测金边 → 有就拖给场上棋子 → 关装备栏
    equip_btn = _flat_roi(rois, "ocr", "equip_btn")
    target = board[0] if board else (bench[0] if bench else None)
    if equip_btn and target:
        ex, ey = builder._roi_mid1000(equip_btn)
        await _execute(Action(type="tap", x1=ex, y1=ey, description="开装备栏"), inp)
        await asyncio.sleep(0.6)
        f2 = screenshot_bgr()
        if f2 is not None:
            items, _, _ = detect_items(f2, rois)
            for slot_name, present in items.items():
                if present:
                    slot_idx = int(slot_name.replace("item", ""))
                    print(f"  装备槽{slot_idx}→棋子 @ {target}")
                    for a in builder.equip_from_slot(slot_idx, target):
                        await _execute(a, inp)
                    await asyncio.sleep(0.4)
        await _execute(Action(type="tap", x1=ex, y1=ey, description="关装备栏"), inp)
        await asyncio.sleep(0.4)

    # 3) 遍历棋子 (棋盘+战备): 点开 → OCR champion 区读名 → 关面板 → 收集名字
    champion_roi = _flat_roi(rois, "ocr", "champion")
    total = len(board) + len(bench)
    print(f"  [遍历] 共 {total} 个棋子 (棋盘{len(board)} + 战备{len(bench)}), 逐个点击:")
    collected = {"棋盘": [], "战备": []}
    names_map: dict = {}                                # {点击位置: 名字}, 供预览可视化
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
                    # 保存识别截图 (以 位置_名字 命名, 便于核对 OCR)
                    save_dir = Path("logs") / "champions"
                    save_dir.mkdir(parents=True, exist_ok=True)
                    safe = name.replace("/", "_").replace("\\", "_") or "unknown"
                    cv2.imencode(".png", crop)[1].tofile(
                        str(save_dir / f"{label}{i}_{safe}.png"))
            collected[label].append(name or "?")
            names_map[pos] = name or "?"
            for a in builder.close_panel():
                await _execute(a, inp)
            await asyncio.sleep(0.4)
    print(f"  === 棋子汇总 ===")
    print(f"  上场({len(collected['棋盘'])}): {collected['棋盘']}")
    print(f"  场下({len(collected['战备'])}): {collected['战备']}")

    # 4) 卖: 战备>5 → 卖最后一个
    if len(bench) > 5:
        last = bench[-1]
        print(f"  战备{len(bench)}个>5, 卖最后一个 @ {last}")
        for a in builder.sell_champion(last):
            await _execute(a, inp)
        await asyncio.sleep(0.8)

    return names_map


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
                    if cx > fw * 0.6:       # 屏幕右侧的玩家列表
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
    last_full_check = 0.0
    shop_closed_this_combat = False
    drops_done_this_combat = False
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
            # 问号掉落物 (模板匹配, 低阈值; 每轮点一次)
            if not drops_done_this_combat:
                drops = await detect_drops_tm(frame, tm, rois, fw, fh)
                if drops:
                    print(f"[战斗] 检出 {len(drops)} 个掉落物, 逐个点击")
                    for dp in drops:
                        print(f"  点掉落物 @ {dp}")
                        for a in builder.click_drop(dp):
                            await _execute(a, inp)
                        await asyncio.sleep(0.4)
                    # 点完走回老巢
                    await _walk_home(builder, inp, last_board, rois, fw, fh)
                    drops_done_this_combat = True
            # 倒数 ≤3s: 走回老巢 (每轮一次)
            if (timer is not None and timer <= WALK_HOME_TIMER
                    and not walked_home_this_combat):
                await _walk_home(builder, inp, last_board, rois, fw, fh)
                walked_home_this_combat = True
        else:
            # 离开战斗 → 重置 per-combat 标志
            shop_closed_this_combat = False
            drops_done_this_combat = False
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
                elif "选秀" in txt:
                    # 选秀: 每 2s 点一次中心, 直到离开选秀界面(最多 15 次)
                    print("[选秀] 检测到, 每 2s 走中心")
                    for _ in range(15):
                        for a in builder.pick_carousel():
                            await _execute(a, inp)
                        await asyncio.sleep(2.0)
                        f2 = screenshot_bgr()
                        if f2 is None:
                            break
                        ok2, buf2 = cv2.imencode(".png", f2)
                        if not ok2 or "选秀" not in ocr_full(buf2.tobytes()).combined_text:
                            break
                    continue

        print(f"[{phase}] stage={stage} timer={timer} ord={tracker.ordinal} "
              f"acted={tracker.acted_this_planning}")

        if phase == "备战" and not tracker.acted_this_planning:
            st = decide_perceive(frame)   # full + 掉落物(?) + 商店开闭
            viz_st = st                   # 预览窗画 OCR/血条
            last_board = st.get("board_clicks", []) or []
            board_n = len(last_board)
            bench_n = len(st.get("bench_clicks", []) or [])
            print(f"  备战: 棋盘{board_n} 战备{bench_n} 店开={st.get('shop_open')} "
                  f"掉落{len(st.get('drops', []) or [])} 装备{st.get('items')}")
            viz_names = await _do_planning(frame, st, builder, inp, rois, fw, fh)
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

            # 血条框 (棋盘+战备, 来自最近感知)
            for bar_key, col in (("board_bars", (0, 255, 0)),
                                 ("bench_bars", (0, 200, 255))):
                for bx in viz_st.get(bar_key, []) or []:
                    cv2.rectangle(disp, (bx[0], bx[1]), (bx[2], bx[3]), col, 2)

            # 棋子名 (在点击位置旁标出识别到的名字)
            for pos, nm in viz_names.items():
                disp = overlay_text(disp, nm, (pos[0] - 30, pos[1] + 12),
                                    color_bgr=(0, 255, 255), px=18, bg_alpha=0.6)

            # 可选: ROI 框
            if SHOW_ROIS:
                for key in ("own_board", "bench", "drop_region", "home",
                            "stage", "timer", "gold", "champion"):
                    roi = _flat_roi(rois, "ocr", key)
                    if roi:
                        cv2.rectangle(disp,
                                      (int(roi[0]*fw), int(roi[1]*fh)),
                                      (int(roi[2]*fw), int(roi[3]*fh)),
                                      (128, 128, 128), 1)

            # 左上信息块: 阶段 + OCR 结果 + 决策摘要
            ocr = viz_st.get("ocr", {}) if viz_st else {}
            lines = [
                f"{phase}  stage={stage}  t={timer}  ord={tracker.ordinal}",
                f"gold={ocr.get('gold','')}  shop={[ocr.get(f'shop{i}','') or '·' for i in range(5)]}",
                f"店开={viz_st.get('shop_open','-')}  装备={viz_st.get('items','-')}  掉落={len(viz_st.get('drops',[]) or []) if viz_st else 0}",
            ]
            if viz_names:
                board_names = [v for k, v in viz_names.items()
                               if k in (viz_st.get("board_clicks") or [])]
                bench_names = [v for k, v in viz_names.items()
                               if k in (viz_st.get("bench_clicks") or [])]
                lines.append(f"上场({len(board_names)}): {board_names}")
                lines.append(f"场下({len(bench_names)}): {bench_names}")
            for i, line in enumerate(lines):
                disp = overlay_text(disp, line, (15, 15 + i * 32),
                                    color_bgr=(255, 255, 255), px=22, bg_alpha=0.55)

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

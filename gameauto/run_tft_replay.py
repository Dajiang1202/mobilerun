#!/usr/bin/env python3
"""TFT 视频回放工作台入口。

用一段 30fps 的 TFT 游戏视频模拟真机推流, 驱动感知/决策正常逻辑,
控制台打印识别结果与决策点。视频墙上时钟异步播放, 算法慢则自动跳帧,
天然模拟实时性。

用法: 直接改下面 ══ 配置区 ══ 里的变量, 然后
    python run_tft_replay.py

换感知/决策方法 = 改 PERCEIVE / DECIDE 变量, 或在下方 PERCEIVE_BACKENDS /
DECIDE_BACKENDS 查表里新增条目。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from gameauto.core.capture.video import VideoCapture
from gameauto.core.orchestration.base import Action
from gameauto.skills.tft.actions import TftActions
from gameauto.skills.tft.workflows import iterate_champions
from gameauto.skills.tft.pregame import ocr_full as _ocr_full_png
from gameauto.tools.replay_driver import (
    ReplayDriver,
    stub_perceive,
    stub_decide,
)

# ═══════════════════════════════════════════════════════════════════════
#  配置区 —— 改这里即可, 不用命令行传参
# ═══════════════════════════════════════════════════════════════════════

# 视频文件路径 (30fps TFT 录像)
VIDEO_PATH = r"D:\gameauto\mobilerun\SVID_20260604_154906_1.mp4"

# OCR 服务: 小ROI走本地 / 全图走远端 (默认都本地, 生产设远端IP)
# 留空则读环境变量 OCR_URL / OCR_FULL_URL
OCR_URL_LOCAL = ""    # 小图(gold/shop/stage)本地OCR, 默认 http://127.0.0.1:8089/ocr
OCR_URL_REMOTE = ""   # 全图(按钮/海克斯/结算)远端OCR, 默认同本地

# 感知 / 决策后端 (见下方 PERCEIVE_BACKENDS / DECIDE_BACKENDS 的可选键)
PERCEIVE = "full"  # "stub" | "ocr" | "champions" | "full" | "adapter"
DECIDE = "tft"       # "stub" | "tft" | "adapter"

# 播放与节奏
SPEED = 1.0          # 播放倍速 (1.0=实时, 2.0=快一倍, 0.5=慢放)
LOOP = False         # 视频结束后是否循环
TICK_INTERVAL = 0.3  # driver tick 最小间隔(s); 0=尽可能快, 由感知限速

# 输出
SHOW = True        # 是否显示 cv2 预览窗
SHOW_DEBUG = False  # 是否显示血条绿色掩膜 debug 窗 (调容差时再开)
RECORD = False       # 是否落盘 (logs/tft_replay_*/...)
SAVE_OCR_IMAGES = True  # 每次OCR调用保存图片到 logs/ocr_debug/ (调试用)
VERBOSE = False      # 打原始 state 全量
QUIET = False        # 只打 actions

# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
#  感知 / 决策后端查表
#  新增方法: 写一个 perceive(frame_bgr)->dict 和 decide(state)->list[Action],
#  在这里登记一个键即可, 然后 --perceive <key> 切换。
# ═══════════════════════════════════════════════════════════════════════

def tft_adapter_perceive(frame_bgr: np.ndarray) -> dict:
    """适配器示例: 把现有 TftPerception.recognize() 包成 perceive 签名。

    默认不接 (现有模板缺失/OCR 慢, 一上来会跑不动)。要接时取消注释并按实际接口调整:

        import cv2
        from gameauto.skills.tft.perception import TftPerception
        # 模块级单例 (避免每帧重建):
        _perception = TftPerception(...)   # 参照 run_tft_scrcpy.py 的构造

        result = _perception.recognize_sync(frame_bgr)   # 同步包装
        return result.parsed if hasattr(result, "parsed") else result
    """
    raise NotImplementedError(
        "tft_adapter_perceive: 接现有 TftPerception 时取消注释实现 (见源码)"
    )


def tft_adapter_decide(state: dict) -> list[Action]:
    """适配器示例: 把现有 TftDecision.decide() 包成 decide 签名。"""
    raise NotImplementedError(
        "tft_adapter_decide: 接现有 TftDecision 时取消注释实现 (见源码)"
    )


def tft_decide(state: dict) -> list[Action]:
    """TFT 决策 (示例): 遍历所有我方棋子 → 点击弹面板。

    用顶层 workflow (iterate_champions) + 中层 TftActions 产出声明式 list[Action]。
    回放里画出来 (tap=圆点/箭头), 真机里 BaseInput 执行 —— 同一份动作, 两端共用。
    state 需含 frame_w/frame_h/board_clicks/bench_clicks (由 full/champions perceive 提供)。
    """
    w = state.get("frame_w"); h = state.get("frame_h")
    if not w or not h:
        return []
    builder = TftActions(_load_rois(), w, h)
    return iterate_champions(
        builder,
        state.get("board_clicks", []),
        state.get("bench_clicks", []),
    )


# ── OCR 感知后端 ──────────────────────────────────────────────────────
# 按 skills/tft/config/rois.yaml 裁剪关键 ROI, 并行 POST 到 OCR 服务 (默认 8089),
# 返回 {roi名: 识别文本}。OCR_URL 环境变量可覆盖服务地址。
# 注意: 用 127.0.0.1 而非 localhost —— Windows→WSL2 时 localhost 会先解析到 IPv6
# (::1), 服务未监听 IPv6, 连接超时 ~15s 才回退, 单请求从 29ms 劣化到 15s。

# 小 ROI OCR: 本地(35ms, 无网络延迟); 全图 OCR: 远端GPU(生产用V100)
# 优先用配置区常量, 留空则读环境变量
if OCR_URL_LOCAL:
    os.environ.setdefault("OCR_URL", OCR_URL_LOCAL)
if OCR_URL_REMOTE:
    os.environ.setdefault("OCR_FULL_URL", OCR_URL_REMOTE)
_OCR_URL = os.environ.get("OCR_URL", "http://127.0.0.1:8089/ocr")
_OCR_FULL_URL = os.environ.get("OCR_FULL_URL", _OCR_URL)  # 全图OCR, 默认同本地
_OCR_SAVE_DIR: str | None = None

def _ocr_save(crop_bgr, tag: str, latency_ms: int, roi_name: str = ""):
    """SAVE_OCR_IMAGES=True 时, 保存每次OCR调用的输入图到 logs/ocr_debug/。"""
    global _OCR_SAVE_DIR
    if not SAVE_OCR_IMAGES:
        return
    if _OCR_SAVE_DIR is None:
        _OCR_SAVE_DIR = str(Path(__file__).parent / "logs" / "ocr_debug")
        Path(_OCR_SAVE_DIR).mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000) % 100000
    label = f"{tag}_{roi_name}" if roi_name else tag
    name = f"{label}_{latency_ms}ms_{ts:05d}.png"
    cv2.imwrite(str(Path(_OCR_SAVE_DIR) / name), crop_bgr)
_OCR_POOL = ThreadPoolExecutor(max_workers=8)
_ROIS_CACHE: dict | None = None
# 要 OCR 的 ROI: (输出键, rois.yaml 中的取值路径)
_OCR_KEYS = [
    ("gold", ["info", "gold"]),
    ("level", ["info", "level"]),
    ("timer", ["info", "round_timer"]),
    ("shop0", ["shop", "slots", 0]),
    ("shop1", ["shop", "slots", 1]),
    ("shop2", ["shop", "slots", 2]),
    ("shop3", ["shop", "slots", 3]),
    ("shop4", ["shop", "slots", 4]),
]

# 即使标了也跳过 OCR (只为点击/定位, 不需要识别文字):
_OCR_EXCLUDE = {
    "hp", "shop_toggle", "continue_btn", "result", "drop_region",
    # 以下纯点击点位, 不需要 OCR
    "own_board", "bench", "home", "equip_btn", "panel_close",
    "item0", "item1", "item2",
    "carousel_pick", "augment_pick0", "augment_pick1", "augment_pick2",
}
# 只有商店开着才 OCR 的 ROI (先检测 refresh_btn 有"刷新", 才 OCR 这些)
_OCR_SHOP_ONLY = {"shop0", "shop1", "shop2", "shop3", "shop4", "refresh_btn", "buy_xp_btn"}


def _load_rois() -> dict:
    global _ROIS_CACHE
    if _ROIS_CACHE is None:
        p = Path(__file__).parent / "skills" / "tft" / "config" / "rois.yaml"
        import yaml
        with open(p, encoding="utf-8") as f:
            _ROIS_CACHE = yaml.safe_load(f)
    return _ROIS_CACHE


def _ocr_image(crop_bgr: np.ndarray, roi_name: str = "") -> tuple[str, int]:
    """POST 一个 BGR 小图到 **本地** OCR 服务 (_OCR_URL), 返回 (combined_text, latency_ms)。

    小 ROI 专用: gold/shop/stage/timer 等, 本地 35ms 无网络延迟。
    roi_name: 调试用, SAVE_OCR_IMAGES 时写入文件名。
    """
    ok, buf = cv2.imencode(".png", crop_bgr)
    if not ok:
        return "", 0
    b64 = base64.b64encode(buf.tobytes()).decode()
    payload = json.dumps(
        {"image": b64, "lang": "ch", "use_angle_cls": True, "threshold": 0.5}
    ).encode()
    req = urllib.request.Request(
        _OCR_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            latency = int(data.get("latency_ms", 0))
            _ocr_save(crop_bgr, "local", latency, roi_name)
            return data.get("combined_text", ""), latency
    except Exception as e:
        return f"<err:{e}>", 0


def _ocr_full_image(frame_bgr: np.ndarray):
    """POST 整帧到 **远端** OCR 服务 (_OCR_FULL_URL), 返回 OcrResult。

    全图专用: 找按钮/结算/海克斯/观战血量, 远端GPU推理快。
    """
    from gameauto.skills.tft.pregame import OcrResult, OcrHit
    ok, buf = cv2.imencode(".png", frame_bgr)
    if not ok:
        return OcrResult()
    b64 = base64.b64encode(buf.tobytes()).decode()
    payload = json.dumps({"image": b64, "threshold": 0.5}).encode()
    req = urllib.request.Request(
        _OCR_FULL_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        hits = [
            OcrHit(text=t, conf=c, box=b)
            for t, c, b in zip(data.get("texts", []), data.get("confidences", []),
                               data.get("boxes", []))
            if b
        ]
        latency = int(data.get("latency_ms", 0))
        _ocr_save(frame_bgr, "remote", latency)
        return OcrResult(hits=hits, latency_ms=latency)
    except Exception:
        return OcrResult()


def ocr_perceive(frame_bgr: np.ndarray) -> dict:
    """OCR 感知: 并行识别各 OCR 区域。

    ROI 来源: 优先 rois.yaml 的 ocr: section (annotate_tft_ocr_rois.py 标注的,
    扁平 key→box); 没有则回退到 _OCR_KEYS 的 info/shop 路径。

    _OCR_EXCLUDE 里的 key 跳过 (hp/shop_toggle/continue_btn/result 暂不识别省算力)。

    返回 state 含:
      - ocr: {roi名: 文本}                 便于决策/简要打印
      - gold/level: int|None               从文本抓的数字
      - ocr_total_ms: int                  各 ROI 服务端推理时长之和
      - overlays: [{box, label}]           预览画框 (box=像素坐标, label="key:text ms")
      - details: [str]                     控制台逐行打印 "key = text (ms)"
    """
    if frame_bgr is None:
        return {}
    rois = _load_rois()
    h, w = frame_bgr.shape[:2]

    # ROI 解析: ocr: section 优先 (排除 _OCR_EXCLUDE), 否则回退到 _OCR_KEYS
    ocr_section = rois.get("ocr") if isinstance(rois, dict) else None
    if ocr_section:
        roi_items = [(key, box) for key, box in ocr_section.items()
                     if key not in _OCR_EXCLUDE]
    else:
        def _resolve(path):
            cur = rois
            for seg in path:
                cur = cur[seg]
            return cur
        roi_items = [(key, _resolve(path)) for key, path in _OCR_KEYS]

    # 条件 OCR: 先单独 OCR refresh_btn 判断商店开没开
    # 商店没开 → shop0-4/refresh/buy_xp 全跳过 (省 7 次 OCR)
    shop_is_open = True   # 默认开, 保守
    refresh_item = None
    for key, roi in roi_items:
        if key == "refresh_btn":
            l = int(roi["left"] * w); t = int(roi["top"] * h)
            r = int(roi["right"] * w); b = int(roi["bottom"] * h)
            txt, _ = _ocr_image(frame_bgr[t:b, l:r], roi_name="refresh_btn")
            shop_is_open = "刷新" in txt
            break
    if not shop_is_open:
        roi_items = [(k, r) for k, r in roi_items if k not in _OCR_SHOP_ONLY]

    # 算出每个 ROI 的像素框 + 裁剪
    items = []
    for key, roi in roi_items:
        if key == "refresh_btn" and shop_is_open:
            continue   # 已经 OCR 过了, 不重复
        try:
            l = int(roi["left"] * w); t = int(roi["top"] * h)
            r = int(roi["right"] * w); b = int(roi["bottom"] * h)
        except Exception:
            continue
        crop = frame_bgr[t:b, l:r]
        items.append((key, (l, t, r, b), crop))

    def _do(it):
        key, box, crop = it
        if crop.size == 0:
            return key, box, "", 0
        txt, ms = _ocr_image(crop, roi_name=key)
        return key, box, txt, ms

    results = list(_OCR_POOL.map(_do, items))

    ocr_text, overlays, details = {}, [], []
    total_ms = 0
    for key, box, txt, ms in results:
        ocr_text[key] = txt
        total_ms += ms
        overlays.append({"box": box, "label": f"{key}:{txt} {ms}ms"})
        details.append(f"{key:6s} = {txt!r:<10} ({ms}ms)")

    return {
        "ocr": ocr_text,
        "gold": _first_int(ocr_text.get("gold", "")),
        "level": _first_int(ocr_text.get("level", "")),
        "ocr_total_ms": total_ms,
        "overlays": overlays,
        "details": details,
    }


def _first_int(text: str) -> int | None:
    """从 OCR 文本里抓第一个整数 (如 'LV.5'→5, '42'→42)。"""
    import re
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


# ── 模板匹配 (问号掉落物) ──────────────────────────────────────────
# 模板在 scale=1 的截图上裁的; 跑游戏/replay 的帧是 scale=2 → 模板要÷2
TEMPLATE_SCALE = 0.5   # 模板放缩因子 (1/游戏SCALE); 模板 scale=1 裁 + 帧 scale=2 → 0.5
_tm_cache = None
_DROP_NAMES = ["drop_blue", "drop_white", "drop_gold"]


def _get_tm():
    """懒加载模板 (带 scale_templates 放缩)。无模板返回 None。"""
    global _tm_cache
    if _tm_cache is not None:
        return _tm_cache
    tpl_dir = Path(__file__).resolve().parent / "skills" / "tft" / "assets" / "templates"
    if not tpl_dir.is_dir():
        return None
    from gameauto.core.perception.cv.template_match import TemplateMatchTask
    _tm_cache = TemplateMatchTask(str(tpl_dir))
    if TEMPLATE_SCALE != 1.0:
        _tm_cache.scale_templates(TEMPLATE_SCALE)
    return _tm_cache


def detect_drops_tm_sync(frame, rois=None, threshold=0.80):
    """同步模板匹配问号掉落物, 返回 [(cx,cy),...] 像素位置。"""
    tm = _get_tm()
    if tm is None:
        return []
    if rois is None:
        rois = _load_rois()
    h, w = frame.shape[:2]
    region = _flat_roi(rois, "ocr", "drop_region") or (0, 0, 1, 1)
    L, T = int(region[0]*w), int(region[1]*h)
    R, B = int(region[2]*w), int(region[3]*h)
    crop = frame[T:B, L:R]
    if crop.size == 0:
        return []
    names = [n for n in _DROP_NAMES if n in tm.template_names]
    if not names:
        return []
    drops = []
    for name in names:
        tpl = tm._templates.get(name)
        if tpl is None:
            continue
        for sc in (0.9, 1.0, 1.1):
            sw, sh = max(1, int(tpl.shape[1]*sc)), max(1, int(tpl.shape[0]*sc))
            if sw > crop.shape[1] or sh > crop.shape[0]:
                continue
            scaled = cv2.resize(tpl, (sw, sh))
            res = cv2.matchTemplate(crop, scaled, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(res >= threshold)
            for x, y in zip(xs, ys):
                cx, cy = int(x + sw//2 + L), int(y + sh//2 + T)
                if all(abs(cx-dx)+abs(cy-dy) > 30 for dx, dy in drops):
                    drops.append((cx, cy))
    return drops


# ── 棋子血条检测 (champions 后端调参区) ──────────────────────────────
# 思路: 我方棋子头顶有绿色血条 (长宽比≥6), 检测到血条 = 定位到棋子。
# 血条下方就是棋子, 点击后 champion 区域弹出棋子名 (真机流程)。
# 回放里先验证检测: 主窗画血条框+点击点, debug 窗画绿色掩膜。
HP_GREEN_RGB = (131, 222, 117)   # 我方血条绿 (RGB)
HP_GREEN_TOL = 30                # RGB 各通道容差; 后排棋子血条更暗(G低到177), 需±30才凑够掩膜
HP_BAR_MIN_RATIO = 6.0           # 血条长宽比下限 (w/h); 实测真图约23
HP_MIN_WIDTH = 12                # 血条最小像素宽 (过滤小噪点)
HP_CLICK_BELOW = 5.0             # 点击点距血条底部 = 血条高度 × 此值 (真机实测稍低更易点中棋子)
HP_OCR_BELOW = False             # 血条下方OCR(血条本身无文字, 默认关; 名字需真机点击弹)
HP_OCR_BELOW_W = 1.2             # OCR 区域宽 = 血条宽 × 此值
HP_OCR_BELOW_H = 1.1             # OCR 区域高 = 血条宽 × 此值 (棋子名/花费区)

# 战备区(bench)血条 — 更短更窄, 阈值单独一组; 棋盘血条仍用上面的 HP_* 参数
BENCH_GREEN_TOL = 30            # 容差(同棋盘, 后排暗绿)
BENCH_BAR_MIN_RATIO = 4.0       # bench 血条更短, 长宽比下限放宽(6→4)
BENCH_MIN_WIDTH = 8             # bench 血条更窄, 最小宽度调小(12→8)


def _flat_roi(rois: dict, section: str, key: str):
    """从 rois(dict) 读 [section][key] 的比例 ROI, 没有返回 None。"""
    sec = rois.get(section) if isinstance(rois, dict) else None
    if not sec:
        return None
    box = sec.get(key)
    if not box:
        return None
    try:
        return (float(box["left"]), float(box["top"]),
                float(box["right"]), float(box["bottom"]))
    except Exception:  # noqa: BLE001
        return None


def _section_as_roi(rois: dict, section: str):
    """section 本身就是比例 ROI (如 rois.yaml 里 bench: {left,...}), 没有返回 None。"""
    box = rois.get(section) if isinstance(rois, dict) else None
    if not box or not isinstance(box, dict):
        return None
    try:
        return (float(box["left"]), float(box["top"]),
                float(box["right"]), float(box["bottom"]))
    except Exception:  # noqa: BLE001
        return None


def _detect_green_bars(crop_bgr: np.ndarray, tol: float = HP_GREEN_TOL,
                       min_ratio: float = HP_BAR_MIN_RATIO,
                       min_width: int = HP_MIN_WIDTH):
    """在 BGR 小图里检测绿色长条, 返回 [(x1,y1,x2,y2), ...] (crop 内坐标) + 掩膜。

    参数化: 棋盘血条用默认值, 战备区血条更短更窄 → 传更小的 min_ratio/min_width。
    """
    r = crop_bgr[:, :, 2].astype(np.int16)
    g = crop_bgr[:, :, 1].astype(np.int16)
    b = crop_bgr[:, :, 0].astype(np.int16)
    gr, gg, gb = HP_GREEN_RGB
    mask = (
        (np.abs(r - gr) <= tol)
        & (np.abs(g - gg) <= tol)
        & (np.abs(b - gb) <= tol)
    ).astype(np.uint8) * 255
    # 水平方向连接血条断段 (桥接内部黑线)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    bars = []
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in cnts:
        x, y, bw, bh = cv2.boundingRect(c)
        if bw < min_width or bh < 2:
            continue
        if bw / bh < min_ratio:
            continue
        bars.append((x, y, x + bw, y + bh))
    return bars, mask


def _bars_in_roi(frame_bgr: np.ndarray, roi: tuple[float, float, float, float],
                 tol: float, min_ratio: float, min_width: int):
    """在整帧的某个比例 ROI 区域检测绿色血条。

    返回 (fbars, mask, (L,T)): fbars 为整帧坐标, mask 为该 crop 的掩膜。
    """
    h, w = frame_bgr.shape[:2]
    L = int(roi[0] * w); T = int(roi[1] * h)
    R = int(roi[2] * w); B = int(roi[3] * h)
    crop = frame_bgr[T:B, L:R]
    if crop.size == 0:
        return [], None, (L, T)
    bars, mask = _detect_green_bars(crop, tol, min_ratio, min_width)
    fbars = [(L + x1, T + y1, L + x2, T + y2) for (x1, y1, x2, y2) in bars]
    return fbars, mask, (L, T)


def _ocr_below_bar(bar, frame_bgr: np.ndarray, w: int, h: int) -> str:
    """OCR 血条下方区域 (棋子名/花费), HP_OCR_BELOW 关时不会被调用。"""
    fx1, fy1, fx2, fy2 = bar
    bw = fx2 - fx1
    cy1 = fy2 + int(bw * 0.2)
    cy2 = cy1 + int(bw * HP_OCR_BELOW_H)
    cx1 = max(0, (fx1 + fx2) // 2 - int(bw * HP_OCR_BELOW_W / 2))
    cx2 = min(w, (fx1 + fx2) // 2 + int(bw * HP_OCR_BELOW_W / 2))
    crop_region = frame_bgr[max(0, cy1):max(0, cy2), cx1:cx2]
    if crop_region.size == 0:
        return ""
    txt, _ = _ocr_image(crop_region, roi_name="hp_bar")
    return txt.strip()


def _bar_click_px(bar, below_mult: float = HP_CLICK_BELOW) -> tuple[int, int]:
    """血条 → 棋子点击点 (血条底部正下方, 像素)。供中层动作/顶层遍历用。"""
    x1, y1, x2, y2 = bar
    cx = (x1 + x2) // 2
    cy = int(y2 + (y2 - y1) * below_mult)
    return (cx, cy)


def _emit_bars(fbars, prefix: str, frame_bgr, w, h):
    """把一组血条转成 overlays + details (含点击点; 可选血条下 OCR)。"""
    overlays, details = [], []
    ocr_texts: list[str] = []
    if HP_OCR_BELOW and fbars:
        ocr_texts = list(_OCR_POOL.map(
            lambda bar: _ocr_below_bar(bar, frame_bgr, w, h), fbars))
    for i, bar in enumerate(fbars):
        fx1, fy1, fx2, fy2 = bar
        overlays.append({"box": (fx1, fy1, fx2, fy2), "label": f"{prefix}{i}"})
        txt = ocr_texts[i] if i < len(ocr_texts) else ""
        if txt:
            overlays.append({"box": (fx1, fy2 + 4, fx2, fy2 + 4), "label": txt})
        cx, cy = _bar_click_px(bar)
        s = 8
        overlays.append({"box": (cx - s, cy - s, cx + s, cy + s), "label": "点"})
        details.append(f"  {prefix}{i} ({fx1},{fy1})-({fx2},{fy2})  点击→({cx},{cy})"
                       + (f"  OCR={txt!r}" if txt else ""))
    return overlays, details


def champions_perceive(frame_bgr: np.ndarray) -> dict:
    """检测我方棋子血条 → 定位棋子 (棋盘 + 战备区)。

    棋盘血条: 搜索 ocr:own_board (没标则整帧), 用 HP_* 阈值。
    战备区血条: 搜索 ocr:bench (没标则 rois.yaml 的 bench:), 用 BENCH_* 阈值(更短更窄)。
    返回 state 含 champion_count(棋盘) / bench_count / overlays / details / debug_image。
    """
    if frame_bgr is None:
        return {}
    rois = _load_rois()
    h, w = frame_bgr.shape[:2]

    # ── 棋盘血条 ──
    board_roi = (_flat_roi(rois, "ocr", "own_board")
                 or (0.0, 0.0, 1.0, 1.0))
    board_bars, board_mask, board_off = _bars_in_roi(
        frame_bgr, board_roi, HP_GREEN_TOL, HP_BAR_MIN_RATIO, HP_MIN_WIDTH)

    # ── 战备区血条 (如有标注; 没标跳过) ──
    bench_roi = (_flat_roi(rois, "ocr", "bench")
                 or _section_as_roi(rois, "bench"))
    bench_bars = []
    if bench_roi:
        bench_bars, _, _ = _bars_in_roi(
            frame_bgr, bench_roi, BENCH_GREEN_TOL, BENCH_BAR_MIN_RATIO, BENCH_MIN_WIDTH)

    overlays, details = [], []
    details.append(f"棋盘血条: {len(board_bars)}  战备血条: {len(bench_bars)}")
    ov, det = _emit_bars(board_bars, "棋盘", frame_bgr, w, h)
    overlays += ov; details += det
    ov, det = _emit_bars(bench_bars, "战备", frame_bgr, w, h)
    overlays += ov; details += det

    # champion 弹名区域 (如有标注), 画出来便于核对几何
    champ_roi = _flat_roi(rois, "ocr", "champion")
    if champ_roi:
        overlays.append({
            "box": (int(champ_roi[0] * w), int(champ_roi[1] * h),
                    int(champ_roi[2] * w), int(champ_roi[3] * h)),
            "label": "champion区",
        })

    # debug 图: 棋盘搜索区的绿色掩膜 (原图色, 其余黑), 调 HP_GREEN_TOL 用
    debug_img = None
    if SHOW_DEBUG and board_mask is not None:
        L, T = board_off
        h2, w2 = frame_bgr.shape[:2]
        R = min(w2, L + board_mask.shape[1]); B = min(h2, T + board_mask.shape[0])
        debug_img = frame_bgr.copy()
        debug_img[T:B, L:R] = cv2.bitwise_and(
            frame_bgr[T:B, L:R], frame_bgr[T:B, L:R], mask=board_mask)

    return {
        "champion_count": len(board_bars),
        "bench_count": len(bench_bars),
        "board_bars": board_bars,           # 像素框, 供 decide 层用
        "bench_bars": bench_bars,
        "board_clicks": [_bar_click_px(b) for b in board_bars],   # 棋子点击点(像素)
        "bench_clicks": [_bar_click_px(b) for b in bench_bars],
        "frame_w": w,                       # 帧分辨率, decide 层换算坐标用
        "frame_h": h,
        "overlays": overlays,
        "details": details,
        "debug_image": debug_img,
    }


def _item_gold_pixels(crop_bgr: np.ndarray) -> int:
    """数装备槽里的金色像素 (有装备=金边)。金: R高 G中高 B偏低 且 R>B。"""
    if crop_bgr is None or crop_bgr.size == 0:
        return 0
    r = crop_bgr[:, :, 2].astype(np.int16)
    g = crop_bgr[:, :, 1].astype(np.int16)
    b = crop_bgr[:, :, 0].astype(np.int16)
    gold = (r > 180) & (g > 150) & (b < 170) & (r > b)
    return int(gold.sum())


# 装备槽金边检测阈值 (金像素超过此 = 有装备; 实测 有~289-849, 无=0)
ITEM_GOLD_MIN_PIXELS = 50


def detect_items(frame_bgr: np.ndarray, rois: dict | None = None):
    """检测装备槽有没有装备 (金边)。返回 {itemN: bool, ...} + overlays/details。"""
    if frame_bgr is None:
        return {}, [], []
    if rois is None:
        rois = _load_rois()
    h, w = frame_bgr.shape[:2]
    items, overlays, details = {}, [], []
    for i in range(3):
        key = f"item{i}"
        roi = _flat_roi(rois, "ocr", key)
        if not roi:
            continue
        L, T = int(roi[0] * w), int(roi[1] * h)
        R, B = int(roi[2] * w), int(roi[3] * h)
        n = _item_gold_pixels(frame_bgr[T:B, L:R])
        present = n >= ITEM_GOLD_MIN_PIXELS
        items[key] = present
        overlays.append({
            "box": (L, T, R, B),
            "label": f"{key}:{'✓装' if present else '空'}({n})",
            "color": (0, 255, 0) if present else (96, 96, 96),
        })
        details.append(f"  {key}: {'有装备' if present else '空'} (金像素{n})")
    return items, overlays, details


def full_perceive(frame_bgr: np.ndarray) -> dict:
    """组合视图: 血条 + 商店/经验/金币 OCR + 装备槽(金边) 一起展示。"""
    ocr_st = ocr_perceive(frame_bgr)
    ch_st = champions_perceive(frame_bgr)
    rois = _load_rois()
    items, item_ov, item_det = detect_items(frame_bgr, rois)
    overlays = (list(ch_st.get("overlays", [])) + list(ocr_st.get("overlays", []))
                + item_ov)
    details = (list(ch_st.get("details", [])) + list(ocr_st.get("details", []))
               + item_det)
    return {
        "champion_count": ch_st.get("champion_count", 0),
        "bench_count": ch_st.get("bench_count", 0),
        "board_clicks": ch_st.get("board_clicks", []),
        "bench_clicks": ch_st.get("bench_clicks", []),
        "frame_w": ch_st.get("frame_w", 0),
        "frame_h": ch_st.get("frame_h", 0),
        "ocr": ocr_st.get("ocr", {}),
        "gold": ocr_st.get("gold"),
        "level": ocr_st.get("level"),
        "items": items,
        "ocr_total_ms": ocr_st.get("ocr_total_ms", 0),
        "overlays": overlays,
        "details": details,
        "debug_image": ch_st.get("debug_image"),
    }


def text_perceive(frame_bgr: np.ndarray) -> dict:
    """全图 OCR: 识别整帧所有文字并框出, 挑出掉落物候选 (含 ? 的)。

    复用 pregame.ocr_full (服务端返回 box)。用于:
      - 全图文字调试 (看屏幕上有什么字、按钮在哪)
      - 装备掉落物检测 (圆形问号 OCR 成 ? 即掉落物)
    比 ocr/full 慢 (整帧 det+rec), 调试用。
    """
    if frame_bgr is None:
        return {}
    h, w = frame_bgr.shape[:2]
    # 掉落物识别区: 标了 ocr:drop_region 就只在该区域内算掉落物 (提速), 没标=全图
    drop_region = _flat_roi(_load_rois(), "ocr", "drop_region")  # None=全图
    ok, buf = cv2.imencode(".png", frame_bgr)
    png = buf.tobytes() if ok else b""
    res = _ocr_full_image(frame_bgr)   # 全图走远端 (_OCR_FULL_URL)
    overlays, details, drops = [], [], []

    def _in_region(cx, cy):
        if not drop_region:
            return True
        l, t, r, b = drop_region
        return (l * w) <= cx <= (r * w) and (t * h) <= cy <= (b * h)

    for hit in res.hits:
        xs = [p[0] for p in hit.box]; ys = [p[1] for p in hit.box]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        is_drop = any(c in hit.text for c in "??？？") and _in_region(cx, cy)
        overlays.append({
            "box": (x0, y0, x1, y1),
            "label": hit.text + (" 💧" if is_drop else ""),
            "color": (0, 0, 255) if is_drop else (255, 255, 0),  # 掉落物红, 普通文字青
        })
        if is_drop:
            drops.append((cx, cy))
            details.append(f"  掉落物? {hit.text!r} @ ({cx},{cy})")
    details.insert(0, f"全图OCR: {len(res.hits)}条文本, 掉落物候选{len(drops)}个 ({res.latency_ms}ms)")
    return {
        "text_count": len(res.hits),
        "drops": drops,
        "drops_count": len(drops),
        "overlays": overlays,
        "details": details,
    }


def decide_perceive(frame_bgr: np.ndarray) -> dict:
    """决策用感知: full_perceive + 掉落物(全图OCR?) + 商店开闭(refresh区有'刷新')。"""
    st = full_perceive(frame_bgr)
    if frame_bgr is None:
        return st
    h, w = frame_bgr.shape[:2]
    rois = _load_rois()
    # 掉落物: 模板匹配 (替 OCR ?, 更准更快)
    st["drops"] = detect_drops_tm_sync(frame_bgr, rois)
    # 商店开闭: refresh_btn 区域 OCR 含「刷新」
    refresh_roi = _flat_roi(rois, "ocr", "refresh_btn")
    rtxt = ""
    if refresh_roi:
        L, T, R, B = (int(refresh_roi[0] * w), int(refresh_roi[1] * h),
                      int(refresh_roi[2] * w), int(refresh_roi[3] * h))
        rtxt, _ = _ocr_image(frame_bgr[T:B, L:R], roi_name="refresh_btn")
    st["shop_open"] = "刷新" in rtxt
    st["refresh_text"] = rtxt
    st["details"] = st.get("details", []) + [
        f"商店开: {st['shop_open']} (refresh={rtxt!r})", f"掉落物: {len(st['drops'])}"]
    return st


def rule_decide(state: dict, builder: TftActions) -> list[Action]:
    """5 条规则的短期决策 (无 AI), 产 list[Action]。state 来自 decide_perceive。

    1. 有问号掉落物 → 逐个点 → 回 drop_region 左上角
    2. 战备区有棋子 → 挨个点
    3. 商店开着 → 随机买一个有字的槽
    4. 装备栏有装备 → 随机拖到棋盘一个棋子
    5. 战备区>3 → 随机卖一个 (长按+垂直拖到底)
    """
    import random
    actions: list[Action] = []
    w = state.get("frame_w", 0); h = state.get("frame_h", 0)
    rois = _load_rois()

    # 1. 问号掉落物
    drops = state.get("drops") or []
    for pos in drops:
        actions += builder.click_drop(pos)
    if drops:
        dr = _flat_roi(rois, "ocr", "drop_region")
        if dr and w:
            actions += builder.click_champion((int(dr[0] * w) + 15, int(dr[1] * h) + 15))

    # 2. 战备区棋子挨个点
    bench = state.get("bench_clicks") or []
    for pos in bench:
        actions += builder.click_champion(pos)

    # 3. 商店开着 → 随机买一个 (有文字的槽)
    if state.get("shop_open"):
        ocr = state.get("ocr", {})
        cands = [i for i in range(5) if ocr.get(f"shop{i}")]
        if cands:
            actions += builder.buy_shop_slot(random.choice(cands))

    # 4. 装备栏有装备 → 随机拖到棋盘一个棋子
    items = state.get("items") or {}
    filled = [int(k[4:]) for k, v in items.items() if v and k.startswith("item")]
    board = state.get("board_clicks") or []
    if filled and board:
        actions += builder.equip_from_slot(random.choice(filled), random.choice(board))

    # 5. 战备区>3 → 随机卖一个 (长按+垂直拖到底)
    if len(bench) > 3:
        actions += builder.sell_champion(random.choice(bench))

    return actions


def rule_decide_backend(state: dict) -> list[Action]:
    """decide 后端包装: 从 state 的帧尺寸建 TftActions, 调 rule_decide。"""
    w = state.get("frame_w"); h = state.get("frame_h")
    if not w or not h:
        return []
    return rule_decide(state, TftActions(_load_rois(), w, h))


PERCEIVE_BACKENDS = {
    "stub": stub_perceive,
    "ocr": ocr_perceive,
    "champions": champions_perceive,
    "text": text_perceive,
    "full": full_perceive,
    "decide": decide_perceive,
    "adapter": tft_adapter_perceive,
}
DECIDE_BACKENDS = {
    "stub": stub_decide,
    "tft": tft_decide,
    "rule": rule_decide_backend,
    "adapter": tft_adapter_decide,
}


# ═══════════════════════════════════════════════════════════════════════

async def main() -> None:
    # Windows 控制台默认 GBK, 强制 UTF-8 让中文 state/日志不乱码;
    # 行缓冲: 管道/重定向时也能实时看到识别结果
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    # ── 校验配置 ──────────────────────────────────────────────────────
    if PERCEIVE not in PERCEIVE_BACKENDS:
        print(f"PERCEIVE={PERCEIVE!r} 无效, 可选: {list(PERCEIVE_BACKENDS)}")
        sys.exit(1)
    if DECIDE not in DECIDE_BACKENDS:
        print(f"DECIDE={DECIDE!r} 无效, 可选: {list(DECIDE_BACKENDS)}")
        sys.exit(1)

    video_path = Path(VIDEO_PATH)
    if not video_path.is_file():
        print(f"视频不存在: {video_path}")
        print(f"请改脚本顶部 VIDEO_PATH 指向你的 TFT 录像")
        sys.exit(1)

    perceive = PERCEIVE_BACKENDS[PERCEIVE]
    decide = DECIDE_BACKENDS[DECIDE]

    capture = VideoCapture(str(video_path), speed=SPEED, loop=LOOP)
    await capture.connect()

    driver = ReplayDriver(
        capture,
        perceive=perceive,
        decide=decide,
        tick_interval=TICK_INTERVAL,
        record=RECORD,
        show=SHOW,
        verbose=VERBOSE,
        quiet=QUIET,
    )

    try:
        await driver.run()
    finally:
        await capture.disconnect()
        # 刷新 stdout, 保证管道/重定向时识别结果 print 不丢
        sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())

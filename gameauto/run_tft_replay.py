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
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from gameauto.core.capture.video import VideoCapture
from gameauto.core.orchestration.base import Action
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

# 感知 / 决策后端 (见下方 PERCEIVE_BACKENDS / DECIDE_BACKENDS 的可选键)
PERCEIVE = "champions"  # "stub" | "ocr" | "champions" | "adapter"
DECIDE = "stub"      # "stub" | "adapter"

# 播放与节奏
SPEED = 1.0          # 播放倍速 (1.0=实时, 2.0=快一倍, 0.5=慢放)
LOOP = False         # 视频结束后是否循环
TICK_INTERVAL = 0.3  # driver tick 最小间隔(s); 0=尽可能快, 由感知限速

# 输出
SHOW = True        # 是否显示 cv2 预览窗
RECORD = False       # 是否落盘 (logs/tft_replay_*/...)
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


# ── OCR 感知后端 ──────────────────────────────────────────────────────
# 按 skills/tft/config/rois.yaml 裁剪关键 ROI, 并行 POST 到 OCR 服务 (默认 8089),
# 返回 {roi名: 识别文本}。OCR_URL 环境变量可覆盖服务地址。
# 注意: 用 127.0.0.1 而非 localhost —— Windows→WSL2 时 localhost 会先解析到 IPv6
# (::1), 服务未监听 IPv6, 连接超时 ~15s 才回退, 单请求从 29ms 劣化到 15s。

_OCR_URL = os.environ.get("OCR_URL", "http://127.0.0.1:8089/ocr")
_OCR_POOL = ThreadPoolExecutor(max_workers=8)
_ROIS_CACHE: dict | None = None
# 要 OCR 的 ROI: (输出键, rois.yaml 中的取值路径)
_OCR_KEYS = [
    ("gold", ["info", "gold"]),
    ("level", ["info", "level"]),
    ("hp", ["info", "hp"]),
    ("timer", ["info", "round_timer"]),
    ("shop0", ["shop", "slots", 0]),
    ("shop1", ["shop", "slots", 1]),
    ("shop2", ["shop", "slots", 2]),
    ("shop3", ["shop", "slots", 3]),
    ("shop4", ["shop", "slots", 4]),
]


def _load_rois() -> dict:
    global _ROIS_CACHE
    if _ROIS_CACHE is None:
        p = Path(__file__).parent / "skills" / "tft" / "config" / "rois.yaml"
        import yaml
        with open(p, encoding="utf-8") as f:
            _ROIS_CACHE = yaml.safe_load(f)
    return _ROIS_CACHE


def _ocr_image(crop_bgr: np.ndarray) -> tuple[str, int]:
    """POST 一个 BGR 小图到 OCR 服务, 返回 (combined_text, latency_ms)。"""
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
            return data.get("combined_text", ""), int(data.get("latency_ms", 0))
    except Exception as e:
        return f"<err:{e}>", 0


def ocr_perceive(frame_bgr: np.ndarray) -> dict:
    """OCR 感知: 并行识别各 OCR 区域。

    ROI 来源: 优先 rois.yaml 的 ocr: section (annotate_tft_ocr_rois.py 标注的,
    扁平 key→box); 没有则回退到 _OCR_KEYS 的 info/shop 路径。

    返回 state 含:
      - ocr: {roi名: 文本}                 便于决策/简要打印
      - gold/level/hp: int|None            从文本抓的数字
      - ocr_total_ms: int                  各 ROI 服务端推理时长之和
      - overlays: [{box, label}]           预览画框 (box=像素坐标, label="key:text ms")
      - details: [str]                     控制台逐行打印 "key = text (ms)"
    """
    if frame_bgr is None:
        return {}
    rois = _load_rois()
    h, w = frame_bgr.shape[:2]

    # ROI 解析: ocr: section 优先 (扁平), 否则回退到 _OCR_KEYS 嵌套路径
    ocr_section = rois.get("ocr") if isinstance(rois, dict) else None
    if ocr_section:
        roi_items = [(key, box) for key, box in ocr_section.items()]
    else:
        def _resolve(path):
            cur = rois
            for seg in path:
                cur = cur[seg]
            return cur
        roi_items = [(key, _resolve(path)) for key, path in _OCR_KEYS]

    # 算出每个 ROI 的像素框 + 裁剪
    items = []
    for key, roi in roi_items:
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
        txt, ms = _ocr_image(crop)
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
        "hp": _first_int(ocr_text.get("hp", "")),
        "ocr_total_ms": total_ms,
        "overlays": overlays,
        "details": details,
    }


def _first_int(text: str) -> int | None:
    """从 OCR 文本里抓第一个整数 (如 'LV.5'→5, '42'→42)。"""
    import re
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


# ── 棋子血条检测 (champions 后端调参区) ──────────────────────────────
# 思路: 我方棋子头顶有绿色血条 (长宽比≥6), 检测到血条 = 定位到棋子。
# 血条下方就是棋子, 点击后 champion 区域弹出棋子名 (真机流程)。
# 回放里先验证检测: 主窗画血条框+点击点, debug 窗画绿色掩膜。
HP_GREEN_RGB = (131, 222, 117)   # 我方血条绿 (RGB)
HP_GREEN_TOL = 30                # RGB 各通道容差; 后排棋子血条更暗(G低到177), 需±30才凑够掩膜
HP_BAR_MIN_RATIO = 6.0           # 血条长宽比下限 (w/h); 实测真图约23
HP_MIN_WIDTH = 12                # 血条最小像素宽 (过滤小噪点)
HP_CLICK_BELOW = 3.0             # 点击点距血条底部 = 血条高度 × 此值


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


def _detect_green_bars(crop_bgr: np.ndarray):
    """在 BGR 小图里检测绿色长条, 返回 [(x1,y1,x2,y2), ...] (crop 内坐标) + 掩膜。"""
    r = crop_bgr[:, :, 2].astype(np.int16)
    g = crop_bgr[:, :, 1].astype(np.int16)
    b = crop_bgr[:, :, 0].astype(np.int16)
    gr, gg, gb = HP_GREEN_RGB
    mask = (
        (np.abs(r - gr) <= HP_GREEN_TOL)
        & (np.abs(g - gg) <= HP_GREEN_TOL)
        & (np.abs(b - gb) <= HP_GREEN_TOL)
    ).astype(np.uint8) * 255
    # 水平方向连接血条断段
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    bars = []
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in cnts:
        x, y, bw, bh = cv2.boundingRect(c)
        if bw < HP_MIN_WIDTH or bh < 2:
            continue
        if bw / bh < HP_BAR_MIN_RATIO:
            continue
        bars.append((x, y, x + bw, y + bh))
    return bars, mask


def champions_perceive(frame_bgr: np.ndarray) -> dict:
    """检测我方棋子血条 → 定位棋子。

    搜索区 ROI 来源 (优先): ocr:own_board (用标注工具画) → board:full → 整帧。
    返回 state 含 champion_count / bars / overlays(血条框+点击点) /
    debug_image(绿色掩膜, SHOW 时单独窗口显示) / details。
    """
    if frame_bgr is None:
        return {}
    rois = _load_rois()
    h, w = frame_bgr.shape[:2]

    roi = (_flat_roi(rois, "ocr", "own_board")
           or _flat_roi(rois, "board", "full")
           or (0.0, 0.0, 1.0, 1.0))
    L = int(roi[0] * w); T = int(roi[1] * h)
    R = int(roi[2] * w); B = int(roi[3] * h)
    crop = frame_bgr[T:B, L:R]
    if crop.size == 0:
        return {"champion_count": 0, "overlays": [], "details": ["搜索区为空"]}

    bars, mask = _detect_green_bars(crop)

    overlays = []
    details = [f"检测到 {len(bars)} 个血条 (搜索区 ocr:own_board)"]
    for (x1, y1, x2, y2) in bars:
        # 转回整帧坐标
        fx1, fy1 = L + x1, T + y1
        fx2, fy2 = L + x2, T + y2
        bar_h = y2 - y1
        overlays.append({"box": (fx1, fy1, fx2, fy2), "label": "血条"})
        # 点击点: 血条底部正下方 (棋子身体), 用小十字标记
        cx = (fx1 + fx2) // 2
        cy = int(fy2 + bar_h * HP_CLICK_BELOW)
        s = 8
        overlays.append({"box": (cx - s, cy - s, cx + s, cy + s), "label": "点"})
        details.append(f"  血条 ({fx1},{fy1})-({fx2},{fy2})  点击→({cx},{cy})")

    # champion 弹名区域 (如有标注), 画出来便于核对几何
    champ_roi = _flat_roi(rois, "ocr", "champion")
    if champ_roi:
        overlays.append({
            "box": (int(champ_roi[0] * w), int(champ_roi[1] * h),
                    int(champ_roi[2] * w), int(champ_roi[3] * h)),
            "label": "champion区",
        })

    # debug 图: 掩膜命中的绿色像素 (原图色, 其余黑), 方便调 HP_GREEN_TOL
    debug_img = cv2.bitwise_and(crop, crop, mask=mask)

    return {
        "champion_count": len(bars),
        "overlays": overlays,
        "details": details,
        "debug_image": debug_img,
    }


PERCEIVE_BACKENDS = {
    "stub": stub_perceive,
    "ocr": ocr_perceive,
    "champions": champions_perceive,
    "adapter": tft_adapter_perceive,
}
DECIDE_BACKENDS = {
    "stub": stub_decide,
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

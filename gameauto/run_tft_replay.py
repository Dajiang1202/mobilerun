#!/usr/bin/env python3
"""TFT 视频回放工作台入口。

用一段 30fps 的 TFT 游戏视频模拟真机推流, 驱动感知/决策正常逻辑,
控制台打印识别结果与决策点。视频墙上时钟异步播放, 算法慢则自动跳帧,
天然模拟实时性。

快速开始:
    python run_tft_replay.py path/to/tft.mp4                  # stub 感知, 1x
    python run_tft_replay.py tft.mp4 --perceive ocr           # OCR 感知 (需 8089 服务)
    python run_tft_replay.py tft.mp4 --speed 2 --show         # 2x 快放 + 预览窗
    python run_tft_replay.py tft.mp4 --perceive adapter       # 接现有 TftPerception
    python run_tft_replay.py tft.mp4 --no-record --quiet      # 只打 actions

换感知/决策方法 = 改下面的 PERCEIVE_BACKENDS / DECIDE_BACKENDS 查表或新增条目。
"""

from __future__ import annotations

import argparse
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


def _ocr_crop(img_bgr: np.ndarray, roi: dict) -> str:
    """裁剪一个 ROI 并 POST 到 OCR 服务, 返回 combined_text。"""
    h, w = img_bgr.shape[:2]
    l = int(roi["left"] * w); t = int(roi["top"] * h)
    r = int(roi["right"] * w); b = int(roi["bottom"] * h)
    crop = img_bgr[t:b, l:r]
    if crop.size == 0:
        return ""
    ok, buf = cv2.imencode(".png", crop)
    if not ok:
        return ""
    b64 = base64.b64encode(buf.tobytes()).decode()
    payload = json.dumps(
        {"image": b64, "lang": "ch", "use_angle_cls": True, "threshold": 0.5}
    ).encode()
    req = urllib.request.Request(
        _OCR_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())["combined_text"]
    except Exception as e:
        return f"<err:{e}>"


def ocr_perceive(frame_bgr: np.ndarray) -> dict:
    """OCR 感知: 并行识别 gold/level/hp/timer + 5 商店槽。"""
    if frame_bgr is None:
        return {}
    rois = _load_rois()

    def _resolve(path):
        cur = rois
        for seg in path:
            cur = cur[seg]
        return cur

    tasks = [(key, _resolve(path)) for key, path in _OCR_KEYS]

    def _do(item):
        key, roi = item
        return key, _ocr_crop(frame_bgr, roi)

    results = list(_OCR_POOL.map(_do, tasks))
    state = {"ocr": dict(results)}
    # 顺手提取数字字段, 方便决策/观察
    state["gold"] = _first_int(state["ocr"].get("gold", ""))
    state["level"] = _first_int(state["ocr"].get("level", ""))
    state["hp"] = _first_int(state["ocr"].get("hp", ""))
    return state


def _first_int(text: str) -> int | None:
    """从 OCR 文本里抓第一个整数 (如 'LV.5'→5, '42'→42)。"""
    import re
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None


PERCEIVE_BACKENDS = {
    "stub": stub_perceive,
    "ocr": ocr_perceive,
    "adapter": tft_adapter_perceive,
}
DECIDE_BACKENDS = {
    "stub": stub_decide,
    "adapter": tft_adapter_decide,
}


# ═══════════════════════════════════════════════════════════════════════

async def main() -> None:
    parser = argparse.ArgumentParser(description="TFT 视频回放工作台")
    parser.add_argument("video", type=str, help="TFT 游戏视频路径 (30fps)")
    parser.add_argument("--speed", type=float, default=1.0, help="播放倍速 (1.0=实时)")
    parser.add_argument("--loop", action="store_true", help="视频循环")
    parser.add_argument("--tick-interval", type=float, default=0.0,
                        help="driver tick 最小间隔(s), 0=尽可能快由感知限速")
    parser.add_argument("--perceive", choices=list(PERCEIVE_BACKENDS), default="stub",
                        help="感知后端")
    parser.add_argument("--decide", choices=list(DECIDE_BACKENDS), default="stub",
                        help="决策后端")
    parser.add_argument("--show", action="store_true", help="显示 cv2 预览窗")
    parser.add_argument("--no-record", action="store_true", help="不落盘")
    parser.add_argument("--verbose", action="store_true", help="打原始 state 全量")
    parser.add_argument("--quiet", action="store_true", help="只打 actions")
    args = parser.parse_args()

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

    video_path = Path(args.video)
    if not video_path.is_file():
        print(f"视频不存在: {video_path}")
        sys.exit(1)

    perceive = PERCEIVE_BACKENDS[args.perceive]
    decide = DECIDE_BACKENDS[args.decide]

    capture = VideoCapture(str(video_path), speed=args.speed, loop=args.loop)
    await capture.connect()

    driver = ReplayDriver(
        capture,
        perceive=perceive,
        decide=decide,
        tick_interval=args.tick_interval,
        record=not args.no_record,
        show=args.show,
        verbose=args.verbose,
        quiet=args.quiet,
    )

    try:
        await driver.run()
    finally:
        await capture.disconnect()
        # 刷新 stdout, 保证管道/重定向时识别结果 print 不丢
        sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())

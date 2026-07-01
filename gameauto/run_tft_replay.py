#!/usr/bin/env python3
"""TFT 视频回放工作台入口。

用一段 30fps 的 TFT 游戏视频模拟真机推流, 驱动感知/决策正常逻辑,
控制台打印识别结果与决策点。视频墙上时钟异步播放, 算法慢则自动跳帧,
天然模拟实时性。

快速开始:
    python run_tft_replay.py path/to/tft.mp4                  # stub 感知, 1x
    python run_tft_replay.py tft.mp4 --speed 2 --show         # 2x 快放 + 预览窗
    python run_tft_replay.py tft.mp4 --perceive adapter       # 接现有 TftPerception
    python run_tft_replay.py tft.mp4 --no-record --quiet      # 只打 actions

换感知/决策方法 = 改下面的 PERCEIVE_BACKENDS / DECIDE_BACKENDS 查表或新增条目。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

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


PERCEIVE_BACKENDS = {
    "stub": stub_perceive,
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

    # Windows 控制台默认 GBK, 强制 UTF-8 让中文 state/日志不乱码
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
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
        # 避免 cv2 / 线程残留挂起
        import os as _os
        _os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())

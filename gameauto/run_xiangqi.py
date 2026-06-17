#!/usr/bin/env python3
"""
GameAuto Xiangqi — 天天象棋全自动入口。

流程:
    截图 → VLM 识别（棋盘+棋子双坐标）→ 引擎搜索最优走法 → 点击走子 → 循环

快速开始:
    python run_xiangqi.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gameauto.config.loader import load_game_config, load_global_config
from gameauto.core.orchestration.base import GameState
from gameauto.core.orchestration.context import GameContext
from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.core.orchestration.loop import GameLoop
from gameauto.core.perception.vlm_client import VlmClient
from gameauto.core.recorder.data_recorder import DataRecorder
from gameauto.core.recorder.session import SessionManager
from gameauto.skills.xiangqi.perception import XiangqiPerception
from gameauto.skills.xiangqi.skill import XiangqiSkill
from gameauto.utils.logging import setup_logging


async def main():
    parser = argparse.ArgumentParser(description="GameAuto Xiangqi")
    parser.add_argument("--config", type=str, help="Path to global config file")
    args = parser.parse_args()

    # ── Config ──────────────────────────────────────────────────────
    global_cfg = load_global_config(args.config)
    vlm_cfg = global_cfg.get("vlm", {})
    if not vlm_cfg.get("api_key") and not vlm_cfg.get("base_url"):
        print("=" * 60)
        print("  VLM not configured!")
        print(f"  Edit: {Path.home()}/.gameauto/settings.yaml")
        print("=" * 60)
        sys.exit(1)

    game_cfg = load_game_config("xiangqi")
    rounds = int(os.environ.get("ROUNDS", game_cfg.get("rounds", 50)))

    # ── Logging & Session ───────────────────────────────────────────
    session = SessionManager(base_dir=Path(__file__).parent / "logs")
    session_dir = session.setup({"game": "xiangqi", "rounds": rounds})
    logger = setup_logging(session_dir, console_level=global_cfg.get("logging", {}).get("console_level", "INFO"))
    logger.info("GameAuto Xiangqi | %d rounds", rounds)
    logger.info("Session: %s", session_dir)

    # ── Device ──────────────────────────────────────────────────────
    device_cfg = global_cfg.get("device", {})
    serial = device_cfg.get("serial")
    from gameauto.core.capture.hdc import HdcCapture as Capture
    from gameauto.core.input.hdc import HdcInput as Input

    capture = Capture(serial)
    await capture.connect()
    input_device = Input(serial)
    await input_device.connect()

    capture_w, capture_h = capture.native_resolution
    input_device.set_input_resolution(capture_w, capture_h)
    logger.info("Device: %dx%d", capture_w, capture_h)

    # ── VLM ─────────────────────────────────────────────────────────
    vlm = VlmClient(
        model=vlm_cfg.get("model", "qwen3-vl-flash"),
        base_url=vlm_cfg.get("base_url", ""),
        api_key=vlm_cfg.get("api_key", ""),
    )
    prompt_path = Path(__file__).parent / "skills" / "xiangqi" / "prompts" / "xiangqi.jinja2"
    perception = XiangqiPerception.from_prompt_file(vlm, str(prompt_path))
    logger.info("VLM: %s | Prompt: %s", vlm_cfg.get("model"), prompt_path)

    # ── Skill + State Machine ───────────────────────────────────────
    skill = XiangqiSkill(perception)
    sm = StateMachine()
    skill.register_states(sm)

    # ── Recorder ────────────────────────────────────────────────────
    recorder = DataRecorder(session)

    # ── Context ─────────────────────────────────────────────────────
    context = GameContext(
        state=GameState.UNKNOWN,
        session_dir=session_dir,
        capture_resolution=(capture_w, capture_h),
        input_resolution=(capture_w, capture_h),
        max_rounds=rounds,
        max_steps_per_round=4,  # tap起点 + wait + tap终点 + wait
    )

    # ── Run ─────────────────────────────────────────────────────────
    loop = GameLoop(capture, input_device, sm, context, recorder)
    try:
        success = await loop.run()
        logger.info("Session complete: %d actions executed", success)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        recorder.record_summary({
            "total_rounds": context.round_num,
            "success_count": context.success_count,
            "final_state": str(context.state),
        })
        await capture.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())

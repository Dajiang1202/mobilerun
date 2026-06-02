#!/usr/bin/env python3
"""
GameAuto DouDiZhu — 斗地主全自动入口。

流程:
    截图 → VLM 识别(叫牌/出牌) → 规则决策 → 执行 → 录制 → 循环

M1 策略:
  - 叫牌阶段: 随机点一个可用按钮
  - 出牌阶段: 点"提示" → 点"出牌"

快速开始:
    python run_doudizhu.py

配置:
    skills/doudizhu/config.yaml         ← 默认参数
    ~/.gameauto/games/doudizhu.yaml     ← 用户覆盖
    ~/.gameauto/settings.yaml           ← 全局 (device + VLM)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from gameauto.config.loader import load_game_config, load_global_config
from gameauto.core.capture.hdc import HdcCapture
from gameauto.core.input.hdc import HdcInput
from gameauto.core.orchestration.base import GameState
from gameauto.core.orchestration.context import GameContext
from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.core.orchestration.loop import GameLoop
from gameauto.core.perception.vlm_client import VlmClient
from gameauto.core.recorder.data_recorder import DataRecorder
from gameauto.core.recorder.session import SessionManager
from gameauto.skills.doudizhu.perception import DouDiZhuPerception
from gameauto.skills.doudizhu.skill import DouDiZhuSkill
from gameauto.utils.logging import setup_logging


async def main():
    # ── Step 0: Parse CLI ────────────────────────────────────────────
    parser = argparse.ArgumentParser(description="GameAuto DouDiZhu")
    parser.add_argument("--config", type=str, help="Path to global config file")
    args = parser.parse_args()

    # ── Step 1: Load config ───────────────────────────────────────────
    global_cfg = load_global_config(args.config)

    vlm_cfg = global_cfg.get("vlm", {})
    if not vlm_cfg.get("api_key") and not vlm_cfg.get("base_url"):
        print("=" * 60)
        print("  VLM not configured!")
        print(f"  Edit: {Path.home()}/.gameauto/settings.yaml")
        print("=" * 60)
        sys.exit(1)

    game_cfg = load_game_config("doudizhu")
    rounds = int(os.environ.get("ROUNDS", game_cfg.get("rounds", 100)))

    # ── Step 2: Setup logging & session ───────────────────────────────
    session = SessionManager(base_dir=Path(__file__).parent / "logs")
    session_dir = session.setup({"game": "doudizhu", "rounds": rounds})
    logger = setup_logging(session_dir, console_level=global_cfg.get("logging", {}).get("console_level", "INFO"))
    logger.info("GameAuto DouDiZhu | %d rounds", rounds)
    logger.info("Session: %s", session_dir)

    # ── Step 3: Connect device ────────────────────────────────────────
    device_cfg = global_cfg.get("device", {})
    capture = HdcCapture(serial=device_cfg.get("serial"), hdc_path=device_cfg.get("hdc_path", "hdc"))
    await capture.connect()
    capture_w, capture_h = capture.native_resolution

    input_device = HdcInput(serial=device_cfg.get("serial"), hdc_path=device_cfg.get("hdc_path", "hdc"))
    await input_device.connect()
    input_device.set_input_resolution(capture_w, capture_h)
    logger.info("Device: %dx%d", capture_w, capture_h)

    # ── Step 4: Setup VLM and perception ──────────────────────────────
    vlm = VlmClient(
        model=vlm_cfg.get("model", "gpt-4o"),
        base_url=vlm_cfg.get("base_url", ""),
        api_key=vlm_cfg.get("api_key", ""),
    )

    prompt_path = Path(__file__).parent / "skills" / "doudizhu" / "prompts" / "doudizhu.jinja2"
    perception = DouDiZhuPerception.from_prompt_file(vlm, str(prompt_path))
    logger.info("VLM: %s | Prompt: %s", vlm_cfg.get("model"), prompt_path)

    # ── Step 5: Setup skill + state machine ───────────────────────────
    skill = DouDiZhuSkill(perception)
    sm = StateMachine()
    skill.register_states(sm)

    # ── Step 6: Setup recorder ────────────────────────────────────────
    recorder = DataRecorder(session)

    # ── Step 7: Context ───────────────────────────────────────────────
    context = GameContext(
        state=GameState.UNKNOWN,
        session_dir=session_dir,
        capture_resolution=(capture_w, capture_h),
        input_resolution=(capture_w, capture_h),
        max_rounds=rounds,
        max_steps_per_round=2,  # 提示 + 出牌
    )

    # ── Step 8: Run ───────────────────────────────────────────────────
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

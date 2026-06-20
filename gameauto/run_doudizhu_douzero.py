#!/usr/bin/env python3
"""
GameAuto DouDiZhu (DouZero) — 斗地主全自动入口。

使用 DouZero 深度蒙特卡洛 AI 做决策，CV 模板匹配做感知。
无需 VLM —— 纯 CV + 深度学习方案。

快速开始:
    # 1. 配置设备 serial（编辑 ~/.gameauto/settings.yaml）
    # 2. 确保模型在 D:/resource/douzero/
    # 3. 准备模板图片放入 skills/doudizhu_douzero/assets/templates/
    # 4. 运行
    python gameauto/run_doudizhu_douzero.py

    # 环境变量覆盖
    ROUNDS=10 python gameauto/run_doudizhu_douzero.py
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
from gameauto.core.recorder.data_recorder import DataRecorder
from gameauto.core.recorder.session import SessionManager
from gameauto.skills.doudizhu_douzero.perception import DouDiZhuDouzeroPerception
from gameauto.skills.doudizhu_douzero.decision import DouzeroDecision
from gameauto.skills.doudizhu_douzero.skill import DouDiZhuDouzeroSkill
from gameauto.utils.logging import setup_logging

# ── 直接在这里配置(无需 CLI / 环境变量) ─────────────────────────────
ROUNDS = 100   # 打几局; 设为 0 则回退到 环境变量 ROUNDS 或 config.yaml 的 max_rounds


async def main():
    # ── Step 0: Parse CLI ────────────────────────────────────────────
    parser = argparse.ArgumentParser(description="GameAuto DouDiZhu (DouZero)")
    parser.add_argument("--config", type=str, help="Path to config file (YAML)")
    args = parser.parse_args()

    # ── Step 1: Load config ───────────────────────────────────────────
    global_cfg = load_global_config(args.config)
    game_cfg = load_game_config("doudizhu_douzero")
    rounds = ROUNDS if ROUNDS else int(os.environ.get("ROUNDS", game_cfg.get("max_rounds", 20)))

    # ── Step 2: Setup logging & session ───────────────────────────────
    session = SessionManager(base_dir=Path(__file__).parent / "logs")
    session_dir = session.setup({
        "game": "doudizhu_douzero",
        "rounds": rounds,
    })
    logger = setup_logging(
        session_dir,
        console_level=global_cfg.get("logging", {}).get("console_level", "INFO"),
    )
    logger.info("GameAuto DouDiZhu (DouZero) | %d rounds", rounds)

    # ── Step 3: Connect device ────────────────────────────────────────
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

    # ── Step 4: Setup perception (CV, NO VLM) ──────────────────────────
    skill_dir = Path(__file__).parent / "skills" / "doudizhu_douzero"
    template_dir = skill_dir / "assets" / "templates"

    perception = DouDiZhuDouzeroPerception(
        template_dir=str(template_dir),
        card_confidence=game_cfg.get("card_confidence", 0.85),
        button_confidence=game_cfg.get("template_confidence", 0.90),
        pass_confidence=game_cfg.get("pass_confidence", 0.90),
    )
    logger.info("Perception: CV template matching | templates=%s", template_dir)

    # ── Step 5: Setup decision (DouZero DeepAgent, NO VLM) ─────────────
    model_dir = game_cfg.get("model_dir", "D:/resource/douzero")
    decision = DouzeroDecision(model_dir=model_dir)
    logger.info("Decision: DouZero DeepAgent | models=%s", model_dir)

    # ── Step 6: Setup skill + state machine ────────────────────────────
    skill = DouDiZhuDouzeroSkill(perception, decision)
    sm = StateMachine()
    skill.register_states(sm)

    # ── Step 7: Setup recorder ────────────────────────────────────────
    recorder = DataRecorder(session)

    # ── Step 8: Context ────────────────────────────────────────────────
    context = GameContext(
        state=GameState.PLAYING,
        session_dir=session_dir,
        capture_resolution=(capture_w, capture_h),
        input_resolution=(capture_w, capture_h),
        max_rounds=rounds,
        max_steps_per_round=9999,  # Card game has many steps per round
    )

    # ── Step 9: Run main loop ─────────────────────────────────────────
    loop = GameLoop(capture, input_device, sm, context, recorder)
    try:
        success = await loop.run()
        logger.info("Session complete: %d rounds", success)
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

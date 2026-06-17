#!/usr/bin/env python3
"""
GameAuto 金铲铲之战 — 全自动入口 (scrcpy 版)。

基于 scrcpy 低延迟推流 + PaddleOCR 感知 + L1 固定策略，
实现金铲铲之战的自动循环：大厅排队 → 备战决策 → 战斗等待。

快速开始:
    # 首次运行会自动创建 ~/.gameauto/settings.yaml
    # 编辑该文件填入设备 serial
    python run_tft_scrcpy.py

    # 自定义参数
    ROUNDS=100 python run_tft_scrcpy.py   # 只跑 100 轮

    # 使用自定义配置
    python run_tft_scrcpy.py --config ~/my_tft_config.yaml

策略配置:
    skills/tft/config.yaml              ← 默认阵容 & 参数
    skills/tft/config/rois.yaml         ← 归一化 ROI 定义
    ~/.gameauto/games/tft.yaml          ← 用户覆盖（可选）

日志结构:
    logs/<session_id>/
    ├── debug.log          ← 完整调试日志
    ├── game.log           ← 人类可读的决策日志
    ├── metadata.json      ← 会话元信息
    └── round_NNN/
        ├── screenshot.png ← 原始截图
        ├── perception.json← 感知结果
        └── ...
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import threading
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).parent.parent))

from gameauto.config.loader import load_game_config, load_global_config
from gameauto.core.capture.scrcpy import ScrcpyCapture as Capture
from gameauto.core.input.scrcpy import ScrcpyInput as Input
from gameauto.core.orchestration.base import GameState
from gameauto.core.orchestration.context import GameContext
from gameauto.core.orchestration.loop import GameLoop
from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.core.perception.cv.ocr import OcrTask
from gameauto.core.perception.cv.template_match import TemplateMatchTask
from gameauto.core.recorder.data_recorder import DataRecorder
from gameauto.core.recorder.session import SessionManager
from gameauto.skills.tft.decision import TftDecision
from gameauto.skills.tft.decision.rules import TftRules
from gameauto.skills.tft.perception import TftPerception
from gameauto.skills.tft.skill import TftSkill
from gameauto.skills.tft.states import TftState
from gameauto.utils.logging import setup_logging


async def main():
    # ── Step 0: Parse CLI ────────────────────────────────────────────
    parser = argparse.ArgumentParser(description="GameAuto 金铲铲之战")
    parser.add_argument("--config", type=str, help="Path to global config (YAML)")
    args = parser.parse_args()

    # ── Step 1: Load config ───────────────────────────────────────────
    global_cfg = load_global_config(args.config)

    # 检查设备是否已配置
    device_cfg = global_cfg.get("device", {})
    serial = device_cfg.get("serial")
    if not serial:
        print("=" * 60)
        print("  Device serial not configured!")
        print(f"  Edit: {Path.home()}/.gameauto/settings.yaml")
        print("  Add: device.serial: \"your_device_serial\"")
        print("=" * 60)
        sys.exit(1)

    # 游戏参数: skills/tft/config.yaml + ~/.gameauto/games/tft.yaml
    game_cfg = load_game_config("tft")
    rounds = game_cfg.get("rounds", 500)
    max_steps = game_cfg.get("max_steps_per_round", 6)
    strategy_cfg = game_cfg.get("strategy", {})
    timing_cfg = game_cfg.get("timing", {})
    perception_cfg = game_cfg.get("perception", {})

    # ── Step 2: Setup logging & session ───────────────────────────────
    session = SessionManager(base_dir=Path(__file__).parent / "logs")
    session_dir = session.setup({
        "game": "tft",
        "max_steps_per_round": max_steps,
        "rounds": rounds,
        "strategy": strategy_cfg.get("type", "slow_roll"),
    })
    logger = setup_logging(
        session_dir,
        console_level=global_cfg.get("logging", {}).get("console_level", "INFO"),
    )
    logger.info("GameAuto 金铲铲之战 | %d rounds, %d max steps/round", rounds, max_steps)
    logger.info("Strategy: %s | Session: %s", strategy_cfg.get("type"), session_dir)

    # ── Step 3: Connect device (scrcpy) ────────────────────────────────
    logger.info("Connecting to device: %s", serial)

    capture = Capture(serial)
    await capture.connect()
    input_device = Input(serial)
    await input_device.connect()

    capture_w, capture_h = capture.native_resolution
    input_device.set_input_resolution(capture_w, capture_h)

    logger.info("Device: %dx%d (native)", capture_w, capture_h)

    # ── Live preview thread ──────────────────────────────────────────
    from gameauto.core.capture.scrcpy.bridge import screenshot_bgr

    _stop_preview = threading.Event()

    def _preview_loop():
        cv2.namedWindow("GameAuto TFT Scrcpy", cv2.WINDOW_NORMAL)
        ow, oh = capture_w, capture_h
        cv2.resizeWindow("GameAuto TFT Scrcpy", max(1, ow // 4), max(1, oh // 4))
        while not _stop_preview.is_set():
            frame = screenshot_bgr()
            if frame is not None:
                cv2.imshow("GameAuto TFT Scrcpy", frame)
            cv2.waitKey(1)
        cv2.destroyAllWindows()

    preview_thread = threading.Thread(target=_preview_loop, daemon=True)
    preview_thread.start()

    # ── Step 4: Setup perception (OCR + template matching, no VLM) ────
    ocr_cfg = perception_cfg.get("ocr", {})
    ocr_task = OcrTask(
        lang=ocr_cfg.get("lang", "ch"),
        use_angle_cls=ocr_cfg.get("use_angle_cls", True),
        gpu=ocr_cfg.get("gpu", True),
    )

    # Template matcher for state detection
    template_dir = Path(__file__).parent / "skills" / "tft" / "assets" / "templates"
    tm_task = TemplateMatchTask(str(template_dir) if template_dir.is_dir() else None)
    if tm_task.template_names:
        logger.info("Loaded %d templates: %s", len(tm_task.template_names), tm_task.template_names)
    else:
        logger.warning("No templates found in %s — state detection will use fallbacks", template_dir)

    # ROIs config
    rois_path = Path(__file__).parent / "skills" / "tft" / "config" / "rois.yaml"
    perception = TftPerception(
        ocr_task=ocr_task,
        tm_task=tm_task,
        rois_path=str(rois_path) if rois_path.exists() else None,
    )
    logger.info("Perception: PaddleOCR + %d templates", len(tm_task.template_names))

    # ── Step 5: Setup decision + skill + state machine ─────────────────
    core_champs = strategy_cfg.get("core_champions", [])
    champion_roles = strategy_cfg.get("champion_roles", {})

    rules = TftRules(
        core_champions=core_champs,
        champion_roles=champion_roles,
        config=strategy_cfg,
    )
    decision = TftDecision(rules)

    skill = TftSkill(perception, decision, tm_task)
    sm = StateMachine()
    skill.register_states(sm)

    logger.info("Registered states: %s", TftState.ALL)
    logger.info("Core champions: %s", [c.get("name") for c in core_champs])

    # ── Step 6: Setup recorder ────────────────────────────────────────
    recorder = DataRecorder(session)

    # ── Step 7: Context ────────────────────────────────────────────────
    context = GameContext(
        state=TftState.LOBBY,  # Start at lobby
        session_dir=session_dir,
        capture_resolution=(capture_w, capture_h),
        input_resolution=(capture_w, capture_h),
        max_rounds=rounds,
        max_steps_per_round=max_steps,
    )

    # ── Step 8: Run main loop ─────────────────────────────────────────
    # Adjust timing for TFT (longer round intervals, combat waits handled by handler)
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
        if _stop_preview:
            _stop_preview.set()
        if preview_thread:
            preview_thread.join(timeout=1)
        await capture.disconnect()
        # JVM shutdown can hang — force exit
        import os as _os
        _os._exit(0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())

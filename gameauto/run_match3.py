#!/usr/bin/env python3
"""
GameAuto Match-3 — 开心消消乐全自动入口。

这是 GameAuto 框架的第一个完整 Skill 实现，展示最小可用闭环:

    截图 → VLM 识别棋盘 → 贪心求解 → 执行 swipe → 录制 → 循环

快速开始:
    # 首次运行会自动创建 ~/.gameauto/settings.yaml
    # 编辑该文件填入 VLM API key 和设备 serial
    python run_match3.py

    # 环境变量覆盖
    MAX_STEPS_PER_ROUND=2 python run_match3.py   # 每轮最多 2 步
    ROUNDS=5 python run_match3.py                 # 只跑 5 轮

游戏参数配置:
    skills/match3/config.yaml             ← 默认参数（max_steps_per_round, rounds）
    ~/.gameauto/games/match3.yaml         ← 用户覆盖（可选，不会被 git 追踪）

设备 & VLM 配置:
    ~/.gameauto/settings.yaml             ← 全局，跨游戏共享

日志结构:
    logs/<session_id>/
    ├── debug.log          ← 完整调试日志
    ├── game.log           ← 人类可读的决策日志
    ├── metadata.json      ← 会话元信息
    ├── summary.json       ← 会话总结
    └── round_NNN/
        ├── screenshot.png ← 原始截图
        ├── board.json     ← VLM 识别的棋盘
        ├── board.png      ← 棋盘网格可视化
        └── swipe.png      ← 滑动箭头可视化

扩展新游戏:
    1. skills/<game>/ 目录下实现 ISkill 接口
    2. 复制 run_match3.py 改名为 run_<game>.py
    3. core/ 完全不动
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
from gameauto.skills.match3.perception import Match3Perception
from gameauto.skills.match3.skill import Match3Skill
from gameauto.utils.logging import setup_logging


async def main():
    # ── Step 0: Parse CLI ────────────────────────────────────────────
    # --config 可以指定自定义全局配置文件
    parser = argparse.ArgumentParser(description="GameAuto Match-3")
    parser.add_argument("--config", type=str, help="Path to config file (YAML)")
    args = parser.parse_args()

    # ── Step 1: Load config ───────────────────────────────────────────
    # 全局配置: ~/.gameauto/settings.yaml → 设备 serial + VLM API key
    # 首次运行会自动创建，需要手动填入 api_key 和 base_url
    global_cfg = load_global_config(args.config)

    # 检查 VLM 是否已配置
    vlm_cfg = global_cfg.get("vlm", {})
    if not vlm_cfg.get("api_key") and not vlm_cfg.get("base_url"):
        print("=" * 60)
        print("  VLM not configured!")
        print(f"  Edit: {Path.home()}/.gameauto/settings.yaml")
        print("=" * 60)
        sys.exit(1)

    # 游戏参数: skills/match3/config.yaml + ~/.gameauto/games/match3.yaml
    # 环境变量 MAX_STEPS_PER_ROUND 和 ROUNDS 优先级最高
    game_cfg = load_game_config("match3")
    max_steps = int(os.environ.get("MAX_STEPS_PER_ROUND", game_cfg.get("max_steps_per_round", 1)))
    rounds = int(os.environ.get("ROUNDS", game_cfg.get("rounds", 10)))

    # ── Step 2: Setup logging & session ───────────────────────────────
    # SessionManager 创建 logs/<timestamp>/ 目录结构
    session = SessionManager(base_dir=Path(__file__).parent / "logs")
    session_dir = session.setup({
        "game": "match3",
        "max_steps_per_round": max_steps,
        "rounds": rounds,
    })
    logger = setup_logging(session_dir, console_level=global_cfg.get("logging", {}).get("console_level", "INFO"))
    logger.info("GameAuto Match-3 | %d rounds, max %d steps/round", rounds, max_steps)
    logger.info("Session: %s", session_dir)

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

    # ── Step 4: Setup VLM and perception ──────────────────────────────
    # VlmClient: 通用 OpenAI-compatible 调用器，temperature=0.2 确保输出稳定
    vlm_cfg = global_cfg.get("vlm", {})
    vlm = VlmClient(
        model=vlm_cfg.get("model", "gpt-4o"),
        base_url=vlm_cfg.get("base_url", ""),
        api_key=vlm_cfg.get("api_key", ""),
    )

    # Match3Perception: 组装 jinja2 prompt → 调 VLM → 解析 JSON → 纠错
    prompt_path = global_cfg.get("perception", {}).get("prompt_path", "")
    if not Path(prompt_path).exists():
        prompt_path = Path(__file__).parent / prompt_path
    perception = Match3Perception.from_prompt_file(vlm, prompt_path)
    logger.info("VLM: %s | Prompt: %s", vlm_cfg.get("model"), prompt_path)

    # ── Step 5: Setup skill + state machine ───────────────────────────
    # Match3Skill 内部封装了 perception → solve → action 的完整流程
    # 通过 StateMachine 注册 IN_GAME 状态的 detector + handler
    skill = Match3Skill(perception, max_steps=max_steps)
    sm = StateMachine()
    skill.register_states(sm)

    # ── Step 6: Setup recorder ────────────────────────────────────────
    # 逐帧录制: 每轮截图、感知结果、决策、操作都会保存到 logs/
    recorder = DataRecorder(session)

    # ── Step 7: Context — 贯穿整个会话的运行时状态 ─────────────────────
    context = GameContext(
        state=GameState.IN_GAME,    # M1: 假设始终在游戏中
        session_dir=session_dir,
        capture_resolution=(capture_w, capture_h),
        input_resolution=(capture_w, capture_h),
        max_rounds=rounds,
        max_steps_per_round=max_steps,
    )

    # ── Step 8: Run main loop ─────────────────────────────────────────
    # GameLoop 驱动完整的 截图→状态机→执行→录制 循环
    loop = GameLoop(capture, input_device, sm, context, recorder)
    try:
        success = await loop.run()
        logger.info("Session complete: %d swipes executed", success)
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

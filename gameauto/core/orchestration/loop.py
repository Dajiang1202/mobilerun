"""GameLoop — 驱动完整的游戏自动化闭环。

每轮执行:
  1. 截图 → 保存原始截图
  2. 状态机 step() → Skill.perceive() + Skill.decide() → Action[]
  3. 逐个执行 Action（swipe/tap/wait）
  4. 步骤间等待动画
  5. 有录制器时保存每帧数据

GameLoop 不关心具体是什么游戏——它只调用 StateMachine 接口。
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback

from gameauto.core.capture.base import BaseCapture
from gameauto.core.input.base import BaseInput
from gameauto.core.orchestration.base import Action
from gameauto.core.orchestration.context import GameContext
from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.core.recorder.data_recorder import DataRecorder

logger = logging.getLogger("gameauto.orchestration.loop")


class GameLoop:
    """游戏主循环。

    接受 capture + input + state_machine + context，驱动自动化流程。
    不包含任何游戏逻辑——所有游戏感知和决策都在 StateMachine 的 handler 中。
    """

    def __init__(
        self,
        capture: BaseCapture,
        input_device: BaseInput,
        state_machine: StateMachine,
        context: GameContext,
        recorder: DataRecorder | None = None,
        step_interval: float = 1.0,
    ) -> None:
        self._capture = capture
        self._input = input_device
        self._sm = state_machine
        self._ctx = context
        self._recorder = recorder
        self._running = False
        self._step_interval = step_interval  # 多步 action 间等待(0=连续点击无间隔)

    async def run(self) -> int:
        """执行主循环直到 max_rounds 或手动停止。

        Returns:
            成功执行的操作总数。
        """
        self._running = True
        t_start = time.time()

        while self._running and self._ctx.round_num < self._ctx.max_rounds:
            self._ctx.round_num += 1
            rnd = self._ctx.round_num
            logger.info("=" * 50)
            logger.info("Round %d/%d", rnd, self._ctx.max_rounds)
            logger.info("=" * 50)

            round_dir = self._ctx.session_dir / f"round_{rnd:03d}"
            round_dir.mkdir(parents=True, exist_ok=True)

            # ── 1. Capture screenshot ──────────────────────────────
            logger.debug("Screenshot...")
            t0 = time.perf_counter()
            img_bytes = await self._capture.screenshot()
            dt = (time.perf_counter() - t0) * 1000
            logger.info("Screenshot: %.1fms, %dB", dt, len(img_bytes))
            (round_dir / "screenshot.png").write_bytes(img_bytes)

            # ── 2. State machine: perceive + decide ─────────────────
            # handler 内部会保存 board 可视化和 swipe 可视化
            actions = await self._sm.step(img_bytes, self._ctx)

            if not actions:
                logger.warning("No actions generated, skipping round")
                continue

            # ── 3. Execute actions ──────────────────────────────────
            for step_idx, action in enumerate(actions):
                logger.info(
                    "Action %d/%d: %s %s",
                    step_idx + 1, len(actions), action.type, action.description,
                )
                try:
                    await self._execute_action(action)
                    self._ctx.success_count += 1
                except Exception:
                    logger.error("Action %d failed:\n%s", step_idx + 1, traceback.format_exc())
                    continue

                # 多步操作之间等待动画（不等待最后一步，外层有统一间隔）
                if step_idx < len(actions) - 1 and self._step_interval > 0:
                    await asyncio.sleep(self._step_interval)

            # ── 4. Wait between rounds ──────────────────────────────
            await asyncio.sleep(0.5)

        elapsed = time.time() - t_start
        logger.info("=" * 50)
        logger.info("Done: %d actions in %d rounds, %.1fs",
                     self._ctx.success_count, self._ctx.max_rounds, elapsed)
        logger.info("=" * 50)

        self._running = False
        return self._ctx.success_count

    async def _execute_action(self, action: Action) -> None:
        """将 Action 转换为实际的设备操作。"""
        if action.type == "swipe":
            await self._input.swipe(
                action.x1, action.y1, action.x2, action.y2, action.duration_ms,
            )
        elif action.type == "tap":
            # tap 按下时长: scrcpy 触控需足够 down-up 间隔 HOS 才识别。
            # 50ms 仍偶发不生效(叫牌/出牌/要不起重复操作), 提到 150ms。
            await self._input.tap(action.x1, action.y1, 150)
        elif action.type == "wait":
            await asyncio.sleep(action.duration_ms / 1000.0)
        elif action.type == "drag":
            # drag 和 swipe 在 HDC 层面实现相同
            await self._input.swipe(
                action.x1, action.y1, action.x2, action.y2, action.duration_ms,
            )
        else:
            logger.warning("Unknown action type: %s", action.type)

    def stop(self) -> None:
        """信号：当前轮结束后停止循环。"""
        self._running = False

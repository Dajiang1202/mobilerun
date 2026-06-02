"""通用游戏状态机 — detector + async handler 注册模式。

状态机本身不包含任何游戏逻辑。每个 Skill 通过 register() 注入自己的
状态检测器和处理器。运行时 step() 遍历已注册状态，找到匹配的就执行。

灵感来自 BetterGI 的 StateMachineBase<TState, TContext> 和
金铲铲 Bot 的 EventDetector 模式。

用法:
    sm = StateMachine()
    sm.register(GameState.IN_GAME,
                detector=is_in_game,        # 同步函数，看截图判断状态
                handler=handle_in_game)      # 异步函数，感知+决策 -> Action[]
    actions = await sm.step(screenshot, context)
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Awaitable, Callable

from gameauto.core.orchestration.base import Action, GameState
from gameauto.core.orchestration.context import GameContext

logger = logging.getLogger("gameauto.orchestration.sm")

# Detector: 同步函数，判断当前截图是否处于某个游戏状态
StateDetector = Callable[[bytes], bool]
# Handler: 异步函数，在该状态下执行感知+决策，返回操作列表
AsyncStateHandler = Callable[[bytes, GameContext], Awaitable[list[Action]]]


class StateMachine:
    """通用有限状态机。

    核心设计:
      - detector 是同步轻量函数（模板匹配/颜色检测），不应做耗时操作
      - handler 是异步函数，可以做 VLM 调用、求解等重操作
      - 状态切换自动记录到日志

    扩展新状态只需要 register()，不需要改 StateMachine 本身。
    """

    def __init__(self) -> None:
        # {GameState: (detector, handler)}
        self._states: dict[GameState, tuple[StateDetector, AsyncStateHandler]] = {}

    def register(
        self,
        state: GameState,
        detector: StateDetector,
        handler: AsyncStateHandler,
    ) -> None:
        """注册一个游戏状态。

        Args:
            state: 游戏状态枚举值
            detector: 同步函数(bytes) -> bool，判断是否是此状态
            handler: 异步函数(bytes, GameContext) -> list[Action]，处理此状态
        """
        self._states[state] = (detector, handler)
        logger.debug("Registered state: %s", state)

    async def step(self, image: bytes, context: GameContext) -> list[Action]:
        """执行一步状态机: 检测当前状态 → 执行对应 handler → 返回 Action 列表。

        这是 GameLoop 每轮调用的核心方法。Handler 返回空列表表示本轮无有效操作。
        """
        # 1. 遍历已注册状态，找到第一个匹配的 detector
        matched_state = GameState.UNKNOWN
        for state, (detector, _handler) in self._states.items():
            try:
                if detector(image):
                    matched_state = state
                    break
            except Exception as e:
                logger.warning("Detector for %s raised: %s", state, e)
        # 2. 状态变化时记录日志
        if matched_state != context.state:
            logger.info("State: %s → %s", context.state, matched_state)
            context.state = matched_state

        # 3. 执行 handler（异步，可以做 VLM 调用等重操作）
        if matched_state in self._states:
            _detector, handler = self._states[matched_state]
            try:
                return await handler(image, context)
            except Exception:
                logger.error("Handler for %s failed:\n%s", matched_state, traceback.format_exc())
                return []

        logger.warning("No handler for state: %s", matched_state)
        return []

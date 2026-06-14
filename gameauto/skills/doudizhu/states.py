"""DouDiZhu state registration — unified single-state handler.

所有画面类型（开局/等待/出牌）由同一个 handler 通过 VLM 感知后路由，
不再区分 BIDDING/PLAYING 两个阶段。
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from pathlib import Path

from gameauto.core.orchestration.base import Action, GameState
from gameauto.core.orchestration.context import GameContext
from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.skills.doudizhu.decision import decide, is_game_over
from gameauto.skills.doudizhu.perception import DouDiZhuPerception
from gameauto.skills.doudizhu.visualizer import annotate_clicks, annotate_game_state

logger = logging.getLogger("gameauto.doudizhu")

PLAYING = GameState.PLAYING  # "playing" — 统一游戏状态


class DouDiZhuStateRegistrar:
    """向状态机注册斗地主的统一游戏状态。

    用法:
        perception = DouDiZhuPerception(vlm, prompt)
        registrar = DouDiZhuStateRegistrar(perception)
        registrar.register(state_machine)
    """

    def __init__(self, perception: DouDiZhuPerception) -> None:
        self._perception = perception

    def register(self, sm: StateMachine) -> None:
        sm.register(PLAYING, detector=self._always, handler=self._handle)

    # ── Detector ───────────────────────────────────────────────────

    def _always(self, image: bytes) -> bool:
        """始终匹配——所有画面由统一 handler 处理。"""
        return True

    # ── Handler ────────────────────────────────────────────────────

    async def _handle(self, image: bytes, context: GameContext) -> list[Action]:
        """统一处理: VLM 感知 → 决策 → 返回操作。

        终止条件:
          - "继续游戏" 按钮检测到 → 设置 context.max_rounds 终止循环
          - round > 20 → 同上
        """
        # ── VLM 感知 ──────────────────────────────────────────────
        t0 = time.time()
        try:
            result = await self._perception.recognize(image)
        except Exception:
            logger.error("VLM call failed:\n%s", traceback.format_exc())
            return []

        state = result.parsed
        latency = (time.time() - t0) * 1000
        screen_type = state.get("screen_type", "unknown")
        n_buttons = len(state.get("buttons", []))
        n_cards = len(state.get("hand_cards", []))
        logger.info("Perception: %.0fms, screen_type=%s, buttons=%d, cards=%d",
                     latency, screen_type, n_buttons, n_cards)

        # ── 保存调试输出 ──────────────────────────────────────────
        round_dir = self._round_dir(context)
        self._save_debug(round_dir, image, state)

        # ── 终止检测 ──────────────────────────────────────────────
        if is_game_over(state, context.round_num):
            logger.info("Game over triggered at round %d — stopping loop", context.round_num)
            context.max_rounds = context.round_num  # 使 GameLoop 下一轮退出
            return []

        # ── 决策 ──────────────────────────────────────────────────
        actions = decide(state, context.round_num)
        self._save_clicks(round_dir, image, state, actions)
        return actions

    # ── Helpers ───────────────────────────────────────────────────

    def _round_dir(self, context: GameContext) -> Path:
        d = context.session_dir / f"round_{context.round_num:03d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_debug(self, round_dir: Path, image: bytes, state: dict) -> None:
        """保存 VLM 原始输出 + 标注可视化。"""
        (round_dir / "vlm_response.txt").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        logger.info("VLM response:\n%s", json.dumps(state, ensure_ascii=False, indent=2))

        if state:
            annotated = annotate_game_state(image, state)
            (round_dir / "perception.png").write_bytes(annotated)

    def _save_clicks(self, round_dir: Path, image: bytes, state: dict, actions: list[Action]) -> None:
        """保存点击序列可视化。"""
        if not actions:
            return
        perception_path = round_dir / "perception.png"
        base_img = perception_path.read_bytes() if perception_path.exists() else image
        annotated = annotate_clicks(base_img, actions)
        (round_dir / "clicks.png").write_bytes(annotated)

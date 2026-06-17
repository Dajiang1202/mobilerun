"""Xiangqi state registration — unified handler for 天天象棋."""

from __future__ import annotations

import json
import logging
import time
import traceback
from pathlib import Path

from gameauto.core.orchestration.base import Action, GameState
from gameauto.core.orchestration.context import GameContext
from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.skills.xiangqi.decision import decide
from gameauto.skills.xiangqi.perception import XiangqiPerception
from gameauto.skills.xiangqi.visualizer import annotate_board_state, annotate_move

logger = logging.getLogger("gameauto.xiangqi")

PLAYING = GameState.PLAYING


class XiangqiStateRegistrar:
    """向状态机注册天天象棋的游戏状态。"""

    def __init__(self, perception: XiangqiPerception) -> None:
        self._perception = perception

    def register(self, sm: StateMachine) -> None:
        sm.register(PLAYING, detector=self._always, handler=self._handle)

    def _always(self, image: bytes) -> bool:
        return True

    async def _handle(self, image: bytes, context: GameContext) -> list[Action]:
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
        n_pieces = len(state.get("pieces", []))
        logger.info("Perception: %.0fms, screen=%s, pieces=%d", latency, screen_type, n_pieces)

        # ── 保存调试输出 ──────────────────────────────────────────
        round_dir = self._round_dir(context)
        self._save_debug(round_dir, image, state)

        # ── 终止条件: round > 50 ──────────────────────────────────
        if context.round_num > 50:
            logger.info("Max rounds (50) reached — stopping")
            context.max_rounds = context.round_num
            return []

        # ── 决策 ──────────────────────────────────────────────────
        actions = decide(state, context.round_num)
        if actions:
            self._save_move_viz(round_dir, image, state, actions)
        return actions

    def _round_dir(self, context: GameContext) -> Path:
        d = context.session_dir / f"round_{context.round_num:03d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_debug(self, round_dir: Path, image: bytes, state: dict) -> None:
        (round_dir / "vlm_response.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        logger.info("VLM response:\n%s", json.dumps(state, ensure_ascii=False, indent=2))
        if state.get("pieces"):
            annotated = annotate_board_state(image, state)
            (round_dir / "perception.png").write_bytes(annotated)

    def _save_move_viz(self, round_dir: Path, image: bytes, state: dict, actions: list[Action]) -> None:
        # 提取点击坐标用于可视化
        taps = [(a.x1, a.y1, a.description or "") for a in actions if a.type == "tap"]
        if len(taps) >= 2:
            move_img = annotate_move(image, state, taps)
            (round_dir / "move.png").write_bytes(move_img)
        if actions:
            from gameauto.skills.xiangqi.visualizer import annotate_clicks
            (round_dir / "clicks.png").write_bytes(annotate_clicks(image, actions))

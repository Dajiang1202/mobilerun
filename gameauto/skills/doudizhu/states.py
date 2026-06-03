"""DouDiZhu state registration — BIDDING and PLAYING states.

BIDDING: 叫地主阶段 — 识别按钮 + 手牌 → 随机点一个按钮
PLAYING: 出牌阶段 — 识别按钮 + 手牌 → 点"提示" → 点"出牌"
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
from gameauto.skills.doudizhu.decision import decide_bidding, decide_playing
from gameauto.skills.doudizhu.perception import DouDiZhuPerception
from gameauto.skills.doudizhu.visualizer import annotate_clicks, annotate_game_state

logger = logging.getLogger("gameauto.doudizhu")

# Use framework-defined card game states
BIDDING = GameState.BIDDING   # "bidding" — 叫地主/抢地主/不加倍
PLAYING = GameState.PLAYING   # "playing" — 出牌阶段

# Buttons that ONLY appear in playing phase (never in bidding)
_PLAYING_ONLY_BUTTONS = {"出牌", "提示"}


def _correct_phase(state: dict, vlm_phase: str) -> str:
    """Fix VLM phase misidentification by checking actual button names."""
    buttons = state.get("buttons", [])
    button_texts = {b.get("text", "") for b in buttons}
    if button_texts & _PLAYING_ONLY_BUTTONS:
        if vlm_phase == "bidding":
            logger.warning("VLM said bidding but playing buttons found — forcing playing")
        return "playing"
    return vlm_phase


class DouDiZhuStateRegistrar:
    """向状态机注册斗地主的两个游戏状态。

    用法:
        perception = DouDiZhuPerception(vlm, prompt)
        registrar = DouDiZhuStateRegistrar(perception)
        registrar.register(state_machine)
    """

    def __init__(self, perception: DouDiZhuPerception) -> None:
        self._perception = perception

    def register(self, sm: StateMachine) -> None:
        sm.register(BIDDING, detector=self._is_bidding, handler=self._handle_bidding)
        sm.register(PLAYING, detector=self._is_playing, handler=self._handle_playing)

    # ── Detectors ──────────────────────────────────────────────────

    def _is_bidding(self, image: bytes) -> bool:
        """M1: always check bidding first, fall back to playing."""
        return False  # 由 _process 统一感知后路由

    def _is_playing(self, image: bytes) -> bool:
        """M1: default phase."""
        return True  # 兜底

    # ── Handlers ───────────────────────────────────────────────────

    def _round_dir(self, context: GameContext) -> Path:
        d = context.session_dir / f"round_{context.round_num:03d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def _perceive_and_route(self, image: bytes) -> tuple[dict, str]:
        """Common: VLM perceive, then route to bidding or playing."""
        t0 = time.time()
        try:
            result = await self._perception.recognize(image)
        except Exception:
            logger.error("VLM call failed:\n%s", traceback.format_exc())
            return {}, "unknown"
        state = result.parsed
        latency = (time.time() - t0) * 1000
        phase = state.get("phase", "playing")
        logger.info("Perception: %.0fms, phase=%s, buttons=%d, cards=%d",
                     latency, phase, len(state.get("buttons", [])), len(state.get("hand_cards", [])))
        return state, phase

    async def _handle_bidding(self, image: bytes, context: GameContext) -> list[Action]:
        """叫地主阶段: VLM 识别 → 随机选按钮。"""
        state, phase = await self._perceive_and_route(image)
        phase = _correct_phase(state, phase)  # fix VLM misidentification
        if phase != "bidding":
            return []

        round_dir = self._round_dir(context)
        self._save_debug(round_dir, image, state)
        actions = decide_bidding(state.get("buttons", []))
        self._save_clicks(round_dir, image, state, actions)
        return actions

    async def _handle_playing(self, image: bytes, context: GameContext) -> list[Action]:
        """出牌阶段: VLM 识别 → 提示 → 出牌。"""
        state, phase = await self._perceive_and_route(image)
        phase = _correct_phase(state, phase)  # fix VLM misidentification
        if phase == "bidding":
            round_dir = self._round_dir(context)
            self._save_debug(round_dir, image, state)
            actions = decide_bidding(state.get("buttons", []))
            self._save_clicks(round_dir, image, state, actions)
            return actions

        round_dir = self._round_dir(context)
        self._save_debug(round_dir, image, state)
        actions = decide_playing(
            state.get("buttons", []),
            state.get("hand_cards", []),
            state.get("last_played", []),
        )
        self._save_clicks(round_dir, image, state, actions)
        return actions

    # ── Visualization helpers ──────────────────────────────────────

    def _save_debug(self, round_dir: Path, image: bytes, state: dict) -> None:
        """Save VLM raw output + annotated state visualization."""
        # VLM 原始输出
        (round_dir / "vlm_response.txt").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        # 打印到控制台
        logger.info("VLM response:\n%s", json.dumps(state, ensure_ascii=False, indent=2))

        # 按钮 + 手牌可视化
        if state:
            annotated = annotate_game_state(image, state)
            (round_dir / "perception.png").write_bytes(annotated)

    def _save_clicks(self, round_dir: Path, image: bytes, state: dict, actions: list[Action]) -> None:
        """Save click sequence visualization."""
        if not actions:
            return
        # 基于 perception.png 叠加点击标记
        perception_path = round_dir / "perception.png"
        base_img = perception_path.read_bytes() if perception_path.exists() else image
        annotated = annotate_clicks(base_img, actions)
        (round_dir / "clicks.png").write_bytes(annotated)

"""DouDiZhu DouZero state registration — multi-state with CV detectors."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from gameauto.core.orchestration.base import Action, GameState
from gameauto.core.orchestration.context import GameContext
from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.skills.doudizhu_douzero.perception import DouDiZhuDouzeroPerception
from gameauto.skills.doudizhu_douzero.decision import DouzeroDecision
from gameauto.skills.doudizhu_douzero.visualizer import annotate_perception, annotate_actions

logger = logging.getLogger("gameauto.doudizhu_douzero")

# Custom game state constants
BIDDING = "bidding"
PLAYING = "playing"
SETTLEMENT = "settlement"
LOBBY = "lobby"


class DouDiZhuDouzeroStateRegistrar:
    """Register DouZero-powered DouDiZhu states with the state machine.

    Three states with CV-based detectors:
      - BIDDING:    Template match for 叫地主/不叫/加倍 buttons
      - PLAYING:    Template match for 出牌 button
      - SETTLEMENT: Template match for 继续 button

    Usage:
        perception = DouDiZhuDouzeroPerception(template_dir=...)
        decision = DouzeroDecision(model_dir=...)
        registrar = DouDiZhuDouzeroStateRegistrar(perception, decision)
        registrar.register(state_machine)
    """

    def __init__(
        self,
        perception: DouDiZhuDouzeroPerception,
        decision: DouzeroDecision,
    ) -> None:
        self._perception = perception
        self._decision = decision

    def register(self, sm: StateMachine) -> None:
        # 优先级: lobby > settlement > bidding > playing(playing 兜底)
        sm.register(LOBBY, detector=self._detect_lobby, handler=self._handle)
        sm.register(SETTLEMENT, detector=self._detect_settlement, handler=self._handle)
        sm.register(BIDDING, detector=self._detect_bidding, handler=self._handle)
        sm.register(PLAYING, detector=self._detect_playing, handler=self._handle)

    # ── Detectors (sync, fast template match) ────────────────────────

    def _detect_lobby(self, image: bytes) -> bool:
        return self._perception.detect_any_button(image, ["开始游戏"], roi_key=None)

    def _detect_bidding(self, image: bytes) -> bool:
        return self._perception.detect_any_button(
            image, ["叫地主", "不叫", "抢地主", "加倍", "不加倍"])

    def _detect_playing(self, image: bytes) -> bool:
        return self._perception.detect_any_button(image, ["出牌", "不出", "要不起"])

    def _detect_settlement(self, image: bytes) -> bool:
        return self._perception.detect_any_button(image, ["继续"], roi_key=None)

    # ── Handler (async, full pipeline) ───────────────────────────────

    async def _handle(self, image: bytes, context: GameContext) -> list[Action]:
        """Common handler for all states: CV perceive → decide → execute."""
        t0 = time.time()

        # 1. CV Perception
        try:
            result = await self._perception.recognize(image)
        except Exception:
            logger.exception("CV perception failed")
            return []

        state = result.parsed
        latency = (time.time() - t0) * 1000
        phase = state.get("phase", "unknown")
        n_cards = len(state.get("my_hand", []))
        n_buttons = len(state.get("buttons", []))
        logger.info("Perception: %.0fms, phase=%s, cards=%d, buttons=%d",
                     latency, phase, n_cards, n_buttons)

        # 2. Save debug output
        round_dir = self._round_dir(context)
        self._save_debug(round_dir, image, state)

        # 3. Auto-initialize round on first playing frame
        if phase == "playing" and not self._decision._round_initialized:
            self._auto_init_round(state)

        # 4. Decision
        actions = self._decision.decide(state)

        # 5. Check game over
        if self._decision.is_round_over:
            winner = self._decision.winner
            logger.info("Round %d over, winner: %s", context.round_num, winner)
            self._decision.reset()

        # 6. Save click visualization + decision detail
        if actions:
            annotated = annotate_actions(image, actions)
            (round_dir / "actions.png").write_bytes(annotated)
        (round_dir / "actions.json").write_text(
            json.dumps(
                [{"step": i + 1, "type": a.type, "x": a.x1, "y": a.y1,
                  "description": a.description} for i, a in enumerate(actions)],
                ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return actions

    def _auto_init_round(self, perception: dict) -> None:
        """Auto-initialize game round from perception data."""
        my_hand = perception.get("my_hand", [])
        is_landlord = perception.get("is_landlord", False)
        landlord_cards = perception.get("landlord_cards", [])

        if not my_hand:
            logger.warning("Cannot auto-init: no hand cards detected")
            return

        # Determine position by card count: landlord has 20, farmers have 17
        if is_landlord:
            my_position = "landlord"
        elif len(my_hand) == 20:
            my_position = "landlord"
        else:
            my_position = "landlord_up"  # default; updated if landlord detected

        try:
            self._decision.init_round(my_hand, landlord_cards, my_position)
            logger.info("Auto-init round: position=%s, hand=%d", my_position, len(my_hand))
        except Exception:
            logger.exception("Auto-init round failed")

    # ── Helpers ───────────────────────────────────────────────────

    def _round_dir(self, context: GameContext) -> Path:
        d = context.session_dir / f"round_{context.round_num:03d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_debug(self, round_dir: Path, image: bytes, state: dict) -> None:
        """Save perception JSON + annotated image."""
        (round_dir / "perception.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if state:
            annotated = annotate_perception(image, state)
            (round_dir / "perception.png").write_bytes(annotated)

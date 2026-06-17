"""TFT decision sub-package.

Phase 1: L1 fixed-strategy rules.
Phase 2: L2 lookup tables + L3 LLM strategic planning.
"""

from __future__ import annotations

import logging
from typing import Any

from gameauto.core.orchestration.base import Action
from gameauto.core.orchestration.context import GameContext
from gameauto.skills.tft.decision.rules import TftRules

logger = logging.getLogger("gameauto.tft.decision")

__all__ = ["TftDecision", "TftRules"]


class TftDecision:
    """决策门面 — 封装所有决策逻辑。

    Usage::

        decision = TftDecision(rules)
        actions = decision.decide(perception_result, context)
    """

    def __init__(self, rules: TftRules) -> None:
        self._rules = rules

    def decide(
        self,
        perception: dict[str, Any],
        context: GameContext,
    ) -> list[Action]:
        """Run decision pipeline on parsed perception data.

        Phase 1: delegates entirely to L1 rules.
        Phase 2: adds L2 lookup and L3 LLM at key decision points.

        Args:
            perception: Parsed perception dict (gold, level, hp, shop_units, ...).
            context: Current game context (round_num, state, etc.).

        Returns:
            Ordered list of Actions to execute.
        """
        actions = self._rules.decide_all(perception, context)

        if actions:
            logger.info(
                "Decision: %d action(s) | gold=%s level=%s hp=%s shop=%s",
                len(actions),
                perception.get("gold"),
                perception.get("level"),
                perception.get("hp"),
                perception.get("shop_units"),
            )

        return actions

    def reset(self) -> None:
        """Reset ownership tracking for a new game."""
        self._rules.reset_owned()

    @property
    def rules(self) -> TftRules:
        return self._rules

"""DouDiZhu decision engine — rule-based strategy.

M1 strategy:
  - Bidding: randomly pick a button
  - Playing: check "提示" button active state
      - active=true → click 提示 → click 出牌
      - active=false → click random card → click 出牌
"""

from __future__ import annotations

import logging
import random

from gameauto.core.orchestration.base import Action

logger = logging.getLogger("gameauto.doudizhu.decision")


def decide_bidding(buttons: list[dict]) -> list[Action]:
    """Bidding phase: randomly pick one button."""
    if not buttons:
        logger.warning("No buttons in bidding phase")
        return []

    preferred = [b for b in buttons if b["text"] in ("叫地主", "抢地主", "不加倍")]
    targets = preferred if preferred else buttons
    chosen = random.choice(targets)

    logger.info("Bidding: '%s' at (%d,%d)", chosen["text"], chosen["x"], chosen["y"])
    return [Action(
        type="tap", x1=chosen["x"], y1=chosen["y"],
        duration_ms=100, description=f"Click '{chosen['text']}'",
    )]


def decide_playing(buttons: list[dict], hand_cards: list[dict]) -> list[Action]:
    """Playing phase: strategy based on 提示 button active state.

    - 提示 active=true  → click 提示 → click 出牌
    - 提示 active=false → click random card → click 出牌
    """
    if not buttons:
        logger.warning("No buttons in playing phase")
        return []

    hint_btn = next((b for b in buttons if b["text"] == "提示"), None)
    play_btn = next((b for b in buttons if b["text"] == "出牌"), None)
    hint_active = hint_btn.get("active", False) if hint_btn else False

    actions = []

    if hint_active:
        logger.info("Playing: hint ACTIVE → click 提示 then 出牌")
        actions.append(Action(
            type="tap", x1=hint_btn["x"], y1=hint_btn["y"],
            duration_ms=150, description="Click '提示' (active)",
        ))
    else:
        logger.info("Playing: hint INACTIVE → click random card then 出牌")
        if hand_cards:
            card = random.choice(hand_cards)
            logger.info("  Card at (%.0f,%.0f)", card["x"], card["y"])
            actions.append(Action(
                type="tap", x1=card["x"], y1=card["y"],
                duration_ms=80, description="Click random card",
            ))

    # Always end with 出牌
    if play_btn:
        actions.append(Action(
            type="tap", x1=play_btn["x"], y1=play_btn["y"],
            duration_ms=150, description="Click '出牌' (play)",
        ))
    elif actions:
        # No 出牌 button — click first available button as fallback
        first = buttons[0]
        actions.append(Action(
            type="tap", x1=first["x"], y1=first["y"],
            duration_ms=100, description=f"Fallback '{first['text']}'",
        ))

    return actions

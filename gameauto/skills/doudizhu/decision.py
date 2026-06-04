"""斗地主决策引擎 — 规则驱动。

M1 策略:
  - 叫牌阶段: 随机点一个按钮
  - 出牌阶段:
      - 提示 disabled 或 桌上无牌(自己是首家) → 随机一张牌 → 出牌
      - 提示 enabled → 点提示 → 出牌
"""

from __future__ import annotations

import logging
import random

from gameauto.core.orchestration.base import Action

logger = logging.getLogger("gameauto.doudizhu.decision")


# Buttons valid in bidding phase
_BIDDING_BUTTONS = {"叫地主", "抢地主", "不叫", "不加倍"}


def decide_bidding(buttons: list[dict]) -> list[Action]:
    """叫牌阶段: 只从叫牌相关按钮中选。"""
    bidding_btns = [b for b in buttons if b["text"] in _BIDDING_BUTTONS]
    if not bidding_btns:
        logger.warning("No bidding buttons found among: %s", [b.get('text') for b in buttons])
        return []

    preferred = [b for b in bidding_btns if b["text"] in ("叫地主", "抢地主", "不加倍")]
    chosen = random.choice(preferred or bidding_btns)

    logger.info("Bidding: '%s' at (%d,%d)", chosen["text"], chosen["x"], chosen["y"])
    return [Action(
        type="tap", x1=chosen["x"], y1=chosen["y"],
        duration_ms=100, description=f"Click '{chosen['text']}'",
    )]


def decide_playing(buttons: list[dict], hand_cards: list[dict], last_played: list[dict]) -> list[Action]:
    """出牌阶段: 随机一张牌 → 提示 → 出牌。"""
    if not buttons:
        logger.warning("No buttons in playing phase")
        return []

    hint_btn = next((b for b in buttons if b["text"] == "提示"), None)
    play_btn = next((b for b in buttons if b["text"] == "出牌"), None)

    actions = []

    # 1) 随机选一张牌
    if hand_cards:
        card = random.choice(hand_cards)
        logger.info("Random card at (%.0f,%.0f)", card["x"], card["y"])
        actions.append(Action(
            type="tap", x1=card["x"], y1=card["y"],
            duration_ms=100, description="Click random card",
        ))

    # 2) 点提示
    if hint_btn:
        actions.append(Action(
            type="tap", x1=hint_btn["x"], y1=hint_btn["y"],
            duration_ms=150, description="Click '提示'",
        ))

    # 3) 点出牌
    if play_btn:
        actions.append(Action(
            type="tap", x1=play_btn["x"], y1=play_btn["y"],
            duration_ms=150, description="Click '出牌'",
        ))

    return actions

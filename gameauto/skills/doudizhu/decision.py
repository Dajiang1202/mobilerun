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
    """出牌阶段决策。

    策略:
      - 提示 enabled  → 点提示 → 出牌 (系统推荐合法牌型)
      - 提示 disabled → 随机选 1 张牌或一对同值牌 → 出牌
    """
    if not buttons:
        logger.warning("No buttons in playing phase")
        return []

    hint_btn = next((b for b in buttons if b["text"] == "提示"), None)
    play_btn = next((b for b in buttons if b["text"] == "出牌"), None)
    hint_enabled = hint_btn.get("enabled", False) if hint_btn else False

    actions = []

    if hint_enabled:
        # 系统可以推荐合法牌型
        logger.info("Playing: hint ENABLED → click 提示 then 出牌")
        actions.append(Action(
            type="tap", x1=hint_btn["x"], y1=hint_btn["y"],
            duration_ms=150, description="Click '提示' (hint)",
        ))
    else:
        # 盲出: 随机选 1 张 or 一对同值牌
        cards_to_play = _pick_cards(hand_cards)
        logger.info("Playing: hint DISABLED → %d card(s) then 出牌", len(cards_to_play))
        for card in cards_to_play:
            name = f"{_suit(card.get('suit',''))}{card.get('value','?')}"
            actions.append(Action(
                type="tap", x1=card["x"], y1=card["y"],
                duration_ms=100, description=f"Click {name}",
            ))

    # 出牌
    if play_btn:
        actions.append(Action(
            type="tap", x1=play_btn["x"], y1=play_btn["y"],
            duration_ms=150, description="Click '出牌' (play)",
        ))

    return actions


def _pick_cards(hand_cards: list[dict]) -> list[dict]:
    """Pick cards to play: either 1 random card, or a random pair of same value."""
    if not hand_cards:
        return []

    # 50% chance: try to play a pair
    if len(hand_cards) >= 2 and random.random() < 0.5:
        pairs = _find_pairs(hand_cards)
        if pairs:
            chosen = random.choice(pairs)
            logger.info("  Playing pair: %s", [_card_str(c) for c in chosen])
            return chosen

    # Fallback: single card
    card = random.choice(hand_cards)
    logger.info("  Playing single: %s", _card_str(card))
    return [card]


def _find_pairs(hand_cards: list[dict]) -> list[list[dict]]:
    """Find all pairs of cards with the same value. Returns list of [card_a, card_b]."""
    from collections import defaultdict
    groups = defaultdict(list)
    for c in hand_cards:
        groups[c.get("value", "?")].append(c)
    return [cards for cards in groups.values() if len(cards) >= 2]


def _card_str(card: dict) -> str:
    return f"{_suit(card.get('suit',''))}{card.get('value','?')}"


def _suit(suit: str) -> str:
    return {"hearts": "H", "spades": "S", "diamonds": "D", "clubs": "C"}.get(suit or "", "?")

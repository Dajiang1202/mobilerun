"""斗地主决策引擎 — 规则驱动。

M1 策略（最简基线，后续迭代增强）:
  - 叫牌阶段: 优先选"叫地主"/"抢地主"，否则随机
  - 出牌阶段: 根据 VLM 识别的"提示"按钮 active 状态决定:
      - active=true  → 点击"提示" → 点击"出牌"（让系统自动选牌）
      - active=false → 随机点一张牌 → 点击"出牌"（盲出）

已知限制:
  - 随机单张可能不合牌型（如上家出对子，你点了单张）→ 需识别牌型后改进
  - 缺少"不出"策略 → 可通过识别"不出"按钮的 active 状态绕开
"""

from __future__ import annotations

import logging
import random

from gameauto.core.orchestration.base import Action

logger = logging.getLogger("gameauto.doudizhu.decision")


def decide_bidding(buttons: list[dict]) -> list[Action]:
    """叫牌阶段决策: 从可见按钮中随机选一个。

    优先选"叫地主"/"抢地主"/"不加倍"（积极策略），
    都没有就随机选一个。
    """
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
    """出牌阶段决策: 根据"提示"按钮的 active 状态选择策略。

    - 提示 active=true  (蓝色/亮色) → 系统可推荐牌型 → 点提示 → 点出牌
    - 提示 active=false (灰色/暗色) → 系统无法推荐 → 随机点一张牌 → 点出牌

    返回 Action 列表，通常 2 个（选牌/提示 + 出牌）。

    TODO: 增加牌型识别 + 合法牌型构造 + "不出"选项
    """
    if not buttons:
        logger.warning("No buttons in playing phase")
        return []

    hint_btn = next((b for b in buttons if b["text"] == "提示"), None)
    play_btn = next((b for b in buttons if b["text"] == "出牌"), None)
    hint_active = hint_btn.get("active", False) if hint_btn else False

    actions = []

    # Step 1: 选择要出的牌（提示 or 随机）
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

    # Step 2: 点击出牌
    if play_btn:
        actions.append(Action(
            type="tap", x1=play_btn["x"], y1=play_btn["y"],
            duration_ms=150, description="Click '出牌' (play)",
        ))
    elif actions:
        # 没有"出牌"按钮时的兜底
        first = buttons[0]
        actions.append(Action(
            type="tap", x1=first["x"], y1=first["y"],
            duration_ms=100, description=f"Fallback '{first['text']}'",
        ))

    return actions

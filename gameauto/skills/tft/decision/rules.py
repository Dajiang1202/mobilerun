"""L1 固定策略规则引擎 — 低费赌狗。

所有决策函数接收感知数据 + 上下文，返回 Action[] 列表。
Actions 使用归一化坐标 [0-1000]，由 GameLoop 在运行时转换为设备像素。
"""

from __future__ import annotations

import logging
import math
import random
from typing import Any

from gameauto.core.orchestration.base import Action, GameState
from gameauto.core.orchestration.context import GameContext

logger = logging.getLogger("gameauto.tft.rules")


# ── Constants ──────────────────────────────────────────────────────────

# Shop slot center coordinates [0-1000] normalized
SHOP_SLOT_X = [160, 320, 500, 680, 840]  # 5 slots, left-to-right
SHOP_SLOT_Y = 900                         # vertical center of shop row

# Button coordinates [0-1000]
REFRESH_BTN = (920, 720)   # shop refresh button
BUY_XP_BTN = (920, 800)    # buy experience button
LOBBY_PLAY_BTN = (500, 900)  # lobby start button

# Board cell centers — 4 rows x 7 cols [0-1000]
_BOARD_LEFT = 80
_BOARD_TOP = 290
_BOARD_RIGHT = 920
_BOARD_BOTTOM = 600
_BOARD_COLS = 7
_BOARD_ROWS = 4
_CELL_W = (_BOARD_RIGHT - _BOARD_LEFT) / _BOARD_COLS
_CELL_H = (_BOARD_BOTTOM - _BOARD_TOP) / _BOARD_ROWS


def _board_cell_center(row: int, col: int) -> tuple[int, int]:
    """Get normalized [0-1000] center of a board cell."""
    x = int(_BOARD_LEFT + (col + 0.5) * _CELL_W)
    y = int(_BOARD_TOP + (row + 0.5) * _CELL_H)
    return x, y


# Champion cost map (1-5 gold)
_CHAMPION_COST: dict[str, int] = {}


def set_champion_costs(costs: dict[str, int]) -> None:
    """Register champion costs from config."""
    _CHAMPION_COST.clear()
    _CHAMPION_COST.update(costs)


# ── Decision facade ────────────────────────────────────────────────────

class TftRules:
    """L1 fixed-strategy rules for TFT low-cost slow-roll.

    Usage::

        rules = TftRules(core_champions, champion_roles, config)
        actions = rules.decide_all(perception, context)
    """

    def __init__(
        self,
        core_champions: list[dict[str, Any]],
        champion_roles: dict[str, list[str]] | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        # Build lookup: champion name → target stars, cost
        self._core: dict[str, dict[str, Any]] = {}
        for champ in core_champions:
            name = champ.get("name", "")
            if name:
                self._core[name] = {
                    "target_stars": champ.get("target_stars", 3),
                    "cost": champ.get("cost", 1),
                    "priority": champ.get("priority", 0),
                }

        self._roles = champion_roles or {}
        self._tanks = set(self._roles.get("tank", []))
        self._carries = set(self._roles.get("carry", []))

        cfg = config or {}
        self._interest_threshold = cfg.get("interest_threshold", 50)
        self._hp_desp = cfg.get("hp_desperation", 30)
        self._target_level = cfg.get("target_level", 6)
        self._level_schedule: dict[str, int] = cfg.get("level_schedule", {})

        # Track owned units across rounds: name → count
        self._owned: dict[str, int] = {}
        # Track board occupancy: set of (row, col)
        self._board_occupied: set[tuple[int, int]] = set()
        # Track current board size (units on board)
        self._board_unit_count: int = 0

    def decide_all(
        self,
        perception: dict[str, Any],
        context: GameContext,
    ) -> list[Action]:
        """Run all L1 decisions for a PLANNING frame.

        Args:
            perception: Parsed perception result (gold, level, hp, shop_units, etc.)
            context: Current game context.

        Returns:
            Ordered list of Actions to execute this round.
        """
        actions: list[Action] = []

        gold = perception.get("gold") or 0
        level = perception.get("level") or 1
        hp = perception.get("hp") or 100
        shop_units: list[str | None] = perception.get("shop_units", [])
        timer_sec = perception.get("timer_seconds")

        # If timer is very low (< 5s), skip complex actions
        if timer_sec is not None and timer_sec < 3:
            logger.debug("Timer low (%ds), skipping actions", timer_sec)
            return actions

        # ── 1. Shop: buy core champions ────────────────────────────
        shop_actions = self._decide_shop(gold, shop_units)
        actions.extend(shop_actions)
        gold_spent = sum(1 for a in shop_actions if a.type == "tap")  # rough estimate

        remaining_gold = gold - gold_spent

        # ── 2. Level up ────────────────────────────────────────────
        # Parse current round from context or timer
        round_key = self._estimate_round(context)

        level_actions = self._decide_level(remaining_gold, level, hp, round_key)
        actions.extend(level_actions)

        # ── 3. Refresh shop (if above interest threshold) ──────────
        refresh_actions = self._decide_refresh(remaining_gold, hp, shop_units)
        actions.extend(refresh_actions)

        # ── 4. Positioning ─────────────────────────────────────────
        position_actions = self._decide_position(level)
        actions.extend(position_actions)

        return actions

    # ── Shop decision ──────────────────────────────────────────────────

    def _decide_shop(
        self, gold: int, shop_units: list[str | None],
    ) -> list[Action]:
        """Decide which shop units to buy."""
        actions: list[Action] = []

        if not shop_units:
            return actions

        for i, unit_name in enumerate(shop_units):
            if unit_name is None:
                continue

            # Fuzzy match against core champions
            matched = self._fuzzy_match_core(unit_name)
            if matched is None:
                continue

            info = self._core[matched]
            cost = info.get("cost", 1)
            target_stars = info.get("target_stars", 3)

            # Check if we already have 3 stars (9 copies)
            owned = self._owned.get(matched, 0)
            needed_for_star = _copies_for_stars(target_stars)
            if owned >= needed_for_star:
                logger.debug("Shop: %s already %d-star, skip", matched, target_stars)
                continue

            # Check if we can afford
            if gold < cost:
                continue

            # Buy it!
            x, y = SHOP_SLOT_X[i], SHOP_SLOT_Y
            actions.append(Action(
                type="tap",
                x1=x, y1=y,
                duration_ms=80 + random.randint(0, 30),
                description=f"Buy {matched} (slot {i})",
            ))

            # Track ownership
            self._owned[matched] = owned + 1
            gold -= cost

        return actions

    # ── Level decision ─────────────────────────────────────────────────

    def _decide_level(
        self, gold: int, level: int, hp: int, round_key: str,
    ) -> list[Action]:
        """Decide whether to buy XP based on fixed schedule."""
        actions: list[Action] = []

        # Find target level for current round
        target = self._get_level_target(round_key)
        if target is None or level >= target:
            return actions

        # Don't level past target_level
        if level >= self._target_level:
            return actions

        # Check gold: need at least interest_threshold before spending
        if hp > self._hp_desp and gold < self._interest_threshold + 4:
            return actions

        x, y = BUY_XP_BTN
        actions.append(Action(
            type="tap",
            x1=x, y1=y,
            duration_ms=80 + random.randint(0, 30),
            description=f"Buy XP (Lv {level} → target {target})",
        ))

        return actions

    def _get_level_target(self, round_key: str) -> int | None:
        """Map stage key (e.g. '2-1') to target level."""
        if not self._level_schedule:
            return None
        return self._level_schedule.get(round_key)

    # ── Refresh decision ───────────────────────────────────────────────

    def _decide_refresh(
        self, gold: int, hp: int, shop_units: list[str | None],
    ) -> list[Action]:
        """Decide whether to refresh the shop."""
        actions: list[Action] = []

        # Normal mode: only refresh if above interest threshold
        if hp > self._hp_desp and gold <= self._interest_threshold:
            return actions

        # Desperation mode: spend down to 30
        if hp <= self._hp_desp and gold <= 30:
            return actions

        # Don't refresh if gold is critically low
        if gold < 4:
            return actions

        # Check if shop already has target units (don't refresh good shop)
        has_target = any(
            u is not None and self._fuzzy_match_core(u) is not None
            for u in (shop_units or [])
        )
        if has_target and hp > self._hp_desp:
            return actions  # Keep good shop

        x, y = REFRESH_BTN
        actions.append(Action(
            type="tap",
            x1=x, y1=y,
            duration_ms=80 + random.randint(0, 30),
            description=f"Refresh shop (gold={gold})",
        ))

        return actions

    # ── Position decision ──────────────────────────────────────────────

    def _decide_position(self, level: int) -> list[Action]:
        """Basic positioning: tanks front row, carries back rows.

        In Phase 1 MVP this is simplified: we assume units are auto-placed.
        Manual positioning (drag from bench to board) will be added once
        bench OCR reliably identifies specific units.

        For now, this is a placeholder that can be extended.
        """
        # Phase 1 MVP: skip positioning for now — units auto-place from shop
        return []

    # ── Helpers ────────────────────────────────────────────────────────

    def _fuzzy_match_core(self, name: str) -> str | None:
        """Match OCR'd name against core champion list using substring.

        Returns the matched champion name (from self._core keys), or None.
        """
        if not name:
            return None

        # Exact match
        if name in self._core:
            return name

        # Substring match (OCR might read partial name)
        for core_name in self._core:
            if core_name in name or name in core_name:
                return core_name

        # Single character overlap (for OCR errors on multi-char names)
        if len(name) >= 2:
            for core_name in self._core:
                common = set(name) & set(core_name)
                if len(common) >= min(len(name), len(core_name)) * 0.6:
                    return core_name

        return None

    def _estimate_round(self, context: GameContext) -> str:
        """Estimate current game stage from round number.

        A full TFT game has stages 1-6, each with ~7 rounds.
        We approximate stage = 1 + context.round_num // 7.
        """
        r = context.round_num
        stage = 1 + r // 7
        sub = 1 + (r % 7)
        return f"{stage}-{sub}"

    def reset_owned(self) -> None:
        """Reset ownership tracking (call at game start)."""
        self._owned.clear()
        self._board_occupied.clear()
        self._board_unit_count = 0


# ── Internal helpers ───────────────────────────────────────────────────

def _copies_for_stars(target_stars: int) -> int:
    """How many total copies needed for N-star: 1★=1, 2★=3, 3★=9."""
    return 3 ** (target_stars - 1)

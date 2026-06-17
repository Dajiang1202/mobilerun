"""DouzeroDecision — wraps DouZero GameEnv + DeepAgent for gameauto pipeline.

Provides a clean interface for the state handler:
  - init_round(): Initialize a new game round with perceived cards
  - decide():      Given perception dict, return Action[] for HDC execution
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from gameauto.core.orchestration.base import Action
from gameauto.skills.doudizhu_douzero.douzero.env.env import (
    Env,
    deck as full_deck,
)
from gameauto.skills.doudizhu_douzero.douzero.env.game import (
    EnvCard2RealCard,
    RealCard2EnvCard,
)
from gameauto.skills.doudizhu_douzero.douzero.deep_agent import DeepAgent

logger = logging.getLogger("gameauto.doudizhu_douzero")


class DouzeroDecision:
    """Decision engine using DouZero DeepAgent model.

    Wraps GameEnv for game state management and DeepAgent for
    optimal action selection.

    Usage:
        decision = DouzeroDecision(model_dir="D:/resource/douzero")
        decision.init_round(my_hand, landlord_cards, my_position)
        actions = decision.decide(perception_dict)
    """

    def __init__(self, model_dir: str) -> None:
        self._model_dir = Path(model_dir)
        self._env: Env | None = None
        self._my_position: str | None = None
        self._round_initialized = False

        # Lazy-loaded agents keyed by position
        self._agents: dict[str, DeepAgent] = {}

    # ------------------------------------------------------------------
    # Agent management
    # ------------------------------------------------------------------

    def _get_agent(self, position: str) -> DeepAgent:
        """Lazy-load DeepAgent for given position."""
        if position not in self._agents:
            ckpt_path = self._model_dir / f"{position}.ckpt"
            if not ckpt_path.exists():
                raise FileNotFoundError(f"Model not found: {ckpt_path}")
            self._agents[position] = DeepAgent(position, str(ckpt_path))
            logger.info("Loaded DeepAgent for %s from %s", position, ckpt_path)
        return self._agents[position]

    # ------------------------------------------------------------------
    # Round lifecycle
    # ------------------------------------------------------------------

    def init_round(
        self,
        my_hand: list[str],
        landlord_cards: list[str],
        my_position: str,
    ) -> None:
        """Initialize a new game round.

        Args:
            my_hand: Our hand cards as display strings (e.g., ['3', 'K', 'A', ...]).
            landlord_cards: 3 revealed landlord cards.
            my_position: 'landlord', 'landlord_up', or 'landlord_down'.
        """
        self._my_position = my_position

        # Convert card strings to env card integers
        my_hand_env = [
            RealCard2EnvCard[c] for c in my_hand if c in RealCard2EnvCard
        ]
        landlord_env = [
            RealCard2EnvCard[c] for c in landlord_cards if c in RealCard2EnvCard
        ]

        # Initialize env with 'wp' objective (win/loss only, no bomb multiplier)
        self._env = Env(objective="wp")

        # Build card_play_data from known info + random fill for opponents.
        # Use Counter to properly subtract only the instances we hold.
        deck_counter = Counter(full_deck)
        deck_counter -= Counter(my_hand_env)
        deck_counter -= Counter(landlord_env)
        # Expand back to a shuffled list
        remaining = list(deck_counter.elements())
        np.random.shuffle(remaining)

        if my_position == "landlord":
            all_hands = {
                "landlord": sorted(my_hand_env),
                "landlord_up": sorted(remaining[:17]),
                "landlord_down": sorted(remaining[17:34]),
            }
        elif my_position == "landlord_up":
            all_hands = {
                "landlord_up": sorted(my_hand_env),
                "landlord": sorted(remaining[:20]),
                "landlord_down": sorted(remaining[20:37]),
            }
        else:  # landlord_down
            all_hands = {
                "landlord_down": sorted(my_hand_env),
                "landlord": sorted(remaining[:20]),
                "landlord_up": sorted(remaining[20:37]),
            }

        self._env._env.card_play_init({
            "landlord": all_hands["landlord"],
            "landlord_up": all_hands["landlord_up"],
            "landlord_down": all_hands["landlord_down"],
            "three_landlord_cards": landlord_env,
        })

        # Seed initial infoset (first acting player is always landlord)
        self._env.infoset = self._env._game_infoset

        self._round_initialized = True
        logger.info(
            "Round initialized: position=%s, hand=%d cards",
            my_position,
            len(my_hand_env),
        )

    def reset(self) -> None:
        """Reset for a new round."""
        self._env = None
        self._my_position = None
        self._round_initialized = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_round_over(self) -> bool:
        """Check if current round has ended."""
        if self._env is None:
            return True
        return self._env._game_over

    @property
    def winner(self) -> str | None:
        """Get winner of current round ('landlord' or 'farmer')."""
        if self._env is None:
            return None
        return self._env._game_winner if self._env._game_over else None

    # ------------------------------------------------------------------
    # Main decision entry point
    # ------------------------------------------------------------------

    def decide(self, perception: dict) -> list[Action]:
        """Core decision: perception -> game state update -> DeepAgent act -> Actions.

        Args:
            perception: Structured dict from DouDiZhuDouzeroPerception.
                Must contain: phase, my_hand, last_play, buttons, card_positions.

        Returns:
            List of Action objects for HDC execution.
        """
        phase = perception.get("phase", "unknown")
        buttons = perception.get("buttons", [])

        if phase == "bidding":
            return self._decide_bidding(buttons)
        elif phase == "settlement":
            return self._decide_settlement(buttons)
        elif phase == "playing":
            return self._decide_playing(perception)
        else:
            logger.warning("Unknown phase: %s", phase)
            return []

    # ------------------------------------------------------------------
    # Phase handlers
    # ------------------------------------------------------------------

    def _decide_bidding(self, buttons: list[dict]) -> list[Action]:
        """Bidding phase: conservative strategy — 不叫, 不加倍."""
        # Priority order: prefer conservative choices first
        priority = ["不叫", "不加倍", "叫地主", "抢地主", "加倍"]
        for text in priority:
            for btn in buttons:
                if text in btn.get("text", ""):
                    return [
                        Action(
                            type="tap",
                            x1=btn["x"],
                            y1=btn["y"],
                            description=f"点击「{text}」",
                        )
                    ]
        logger.warning("No recognized bidding button found among: %s",
                       [b.get("text") for b in buttons])
        return []

    def _decide_settlement(self, buttons: list[dict]) -> list[Action]:
        """Settlement phase: click '继续' to advance to next round."""
        for btn in buttons:
            if "继续" in btn.get("text", ""):
                return [
                    Action(
                        type="tap",
                        x1=btn["x"],
                        y1=btn["y"],
                        description="点击「继续」",
                    )
                ]
        logger.warning("No '继续' button found in settlement")
        return []

    def _decide_playing(self, perception: dict) -> list[Action]:
        """Playing phase: use DeepAgent to decide optimal play."""
        if self._env is None:
            logger.error("Game not initialized — call init_round() first")
            return []

        acting_pos = self._env._acting_player_position

        if acting_pos != self._my_position:
            # ---- Opponent's turn: record their move from perception ----
            return self._handle_opponent_turn(acting_pos, perception)

        # ---- Our turn: query DeepAgent ----
        try:
            agent = self._get_agent(self._my_position)
            infoset = self._env.infoset
            action_cards, confidence = agent.act(infoset)
        except Exception:
            logger.exception("DeepAgent.act failed")
            return self._fallback_pass(perception)

        # Log decision
        display_cards = [EnvCard2RealCard.get(c, "?") for c in action_cards]
        try:
            conf_val = float(confidence.item() if hasattr(confidence, "item") else confidence)
        except (TypeError, ValueError):
            conf_val = 0.0
        logger.info(
            "DeepAgent decision: %s -> %s (confidence=%.4f)",
            self._my_position,
            "".join(display_cards) if display_cards else "pass",
            conf_val,
        )

        if not action_cards:
            return self._action_pass(perception)

        # Execute the play through the game engine
        self._env.players[acting_pos].set_action(action_cards)
        self._env._env.step()
        self._env.infoset = self._env._game_infoset

        return self._build_play_actions(action_cards, perception)

    # ------------------------------------------------------------------
    # Opponent turn handling
    # ------------------------------------------------------------------

    def _handle_opponent_turn(
        self, acting_pos: str, perception: dict
    ) -> list[Action]:
        """Record opponent's move from perception and step the env."""
        last_play = perception.get("last_play", [])
        is_pass = perception.get("is_pass", False)

        if is_pass:
            self._opponent_pass(acting_pos)
        elif last_play:
            self._opponent_play(acting_pos, last_play)
        else:
            # Ambiguous: no cards detected but also no pass marker.
            # This can happen on the very first frame before the opponent
            # has acted.  Do not update game state — wait for next frame.
            return []

        self._env.infoset = self._env._game_infoset
        return []  # No HDC actions to execute for opponent moves

    def _opponent_pass(self, position: str) -> None:
        """Record opponent pass in game state."""
        if self._env:
            self._env.players[position].set_action([])
            self._env._env.step()

    def _opponent_play(self, position: str, cards: list[str]) -> None:
        """Record opponent card play in game state."""
        if not self._env:
            return
        env_cards = [
            RealCard2EnvCard.get(c) for c in cards if c in RealCard2EnvCard
        ]
        if env_cards:
            self._env.players[position].set_action(env_cards)
            self._env._env.step()

    # ------------------------------------------------------------------
    # Action builders (our turn -> HDC taps)
    # ------------------------------------------------------------------

    def _build_play_actions(
        self, action_cards: list[int], perception: dict
    ) -> list[Action]:
        """Build tap actions to select cards and press '出牌'."""
        actions: list[Action] = []
        card_positions = perception.get("card_positions", {})

        # Tap each card in the action set
        for card in action_cards:
            card_name = EnvCard2RealCard.get(card, str(card))
            pos = card_positions.get(card_name)
            if pos:
                actions.append(
                    Action(
                        type="tap",
                        x1=pos[0],
                        y1=pos[1],
                        description=f"选牌 {card_name}",
                    )
                )

        # Tap "出牌" button to confirm
        buttons = perception.get("buttons", [])
        for btn in buttons:
            if "出牌" in btn.get("text", ""):
                actions.append(
                    Action(
                        type="tap",
                        x1=btn["x"],
                        y1=btn["y"],
                        description="点击「出牌」",
                    )
                )
                break
        else:
            logger.warning(
                "No '出牌' button found among: %s",
                [b.get("text") for b in buttons],
            )

        return actions

    def _action_pass(self, perception: dict) -> list[Action]:
        """Record a pass in game state and return tap on '不出' button."""
        if self._env:
            self._env.players[self._my_position].set_action([])
            self._env._env.step()
            self._env.infoset = self._env._game_infoset

        # Find and tap the "不出" button
        buttons = perception.get("buttons", [])
        for btn in buttons:
            if "不出" in btn.get("text", ""):
                return [
                    Action(
                        type="tap",
                        x1=btn["x"],
                        y1=btn["y"],
                        description="点击「不出」",
                    )
                ]

        logger.warning(
            "No '不出' button found — returning empty action list"
        )
        return []

    def _fallback_pass(self, perception: dict) -> list[Action]:
        """Fallback: pass when DeepAgent fails."""
        logger.warning("DeepAgent failed, falling back to pass")
        return self._action_pass(perception)

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

        if phase == "lobby":
            return self._decide_lobby(buttons)
        elif phase == "bidding":
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

    @staticmethod
    def _wait(ms: int) -> Action:
        """生成 wait 动作(等发牌/出牌动画), 期间不截图不识别, 省轮次和算力。"""
        return Action(type="wait", duration_ms=ms, description=f"等待{ms // 1000}s(动画)")

    def _decide_lobby(self, buttons: list[dict]) -> list[Action]:
        """大厅: 点击「开始游戏」进入对局。"""
        for btn in buttons:
            if "开始游戏" in btn.get("text", ""):
                return [
                    Action(
                        type="tap",
                        x1=btn["x"],
                        y1=btn["y"],
                        description="点击「开始游戏」",
                    ),
                    self._wait(6000),   # 发牌动画, 6s 内无需操作
                ]
        logger.warning("No '开始游戏' button found in lobby")
        return []

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
                        ),
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
                    ),
                ]
        logger.warning("No '继续' button found in settlement")
        return []

    def _decide_playing(self, perception: dict) -> list[Action]:
        """Playing phase: use DeepAgent to decide optimal play."""
        # 游戏显示「要不起」= 当前压不住对手的牌, 直接 pass。
        # 以 UI 为准, 不依赖 env 状态(env 靠逐帧 perception 推进, 一旦漏识别
        # 对手出牌就会偏离, 误判成「自由出牌」而乱出更小的牌)。
        button_names = [b.get("text", "") for b in perception.get("buttons", [])]
        if any("要不起" in n for n in button_names):
            logger.info("对手牌压不住(UI 显示要不起), 直接 pass")
            return self._action_pass(perception)

        if self._env is None:
            logger.error("Game not initialized — call init_round() first")
            return []

        # 方案A: 每帧用屏幕 last_move 校准 env(纠正跨帧漂移), 再让 DeepAgent 决策。
        # 校准把 acting 拉回我方, 不再走 _handle_opponent_turn 逐帧推进(那是漂移根源)。
        try:
            self._calibrate_env(perception)
        except Exception:
            logger.exception("env calibration failed")
        if self._env._acting_player_position != self._my_position:
            logger.warning("校准后 acting 仍非我方, fallback pass")
            return self._fallback_pass(perception)

        # 决策: 临时规则(_USE_RULE=True)—— 方案A 的 env 不完整, DouZero 偏保守
        # (该压不压)。规则「能压就压、出偏小、留炸弹底」更稳。设 False 换回 DouZero。
        try:
            legal = self._env.infoset.legal_actions
            if self._USE_RULE:
                action_cards = self._rule_select(legal)
                conf_val, src = -1.0, "rule"
            else:
                agent = self._get_agent(self._my_position)
                action_cards, confidence = agent.act(self._env.infoset)
                try:
                    conf_val = float(confidence.item() if hasattr(confidence, "item") else confidence)
                except (TypeError, ValueError):
                    conf_val = 0.0
                src = "douzero"
        except Exception:
            logger.exception("decide failed")
            return self._fallback_pass(perception)

        display_cards = [EnvCard2RealCard.get(c, "?") for c in action_cards]
        logger.info("[%s] %s -> %s (conf=%.3f, legal=%d)", src, self._my_position,
                    "".join(display_cards) if display_cards else "pass", conf_val, len(legal))

        if not action_cards:
            return self._action_pass(perception)

        # 单步模式: 不 step env(每帧重新校准), 直接选牌出牌
        return self._build_play_actions(action_cards, perception)

    # ------------------------------------------------------------------
    # 方案A: 每帧用屏幕 last_move 校准 env, 纠正跨帧漂移
    # ------------------------------------------------------------------
    _ORDER = ("landlord", "landlord_down", "landlord_up")  # 出牌顺序
    _USE_RULE = True  # 临时规则决策(方案A env 不准时 DouZero 偏保守); False 换回 DouZero

    def _rule_select(self, legal_actions: list[list[int]]) -> list[int]:
        """临时规则决策: 能压就压(不轻易 pass), 出偏小的牌, 留炸弹/王炸作底。

        自由出优先组合牌型(顺子/连对/三带二/三带一/顺三), 不轻易出散单张;
        同优先级选点数小的(保守出小)。压牌时 legal 是同牌型, 自然选最小能压。
        """
        non_pass = [a for a in legal_actions if a]
        if not non_pass:
            return []  # 只能 pass
        bombs = [a for a in non_pass if len(a) == 4 and len(set(a)) == 1]      # 炸弹(4 同点)
        rockets = [a for a in non_pass if sorted(a) == [20, 30]]               # 王炸(小王+大王)
        non_bomb = [a for a in non_pass if a not in bombs and a not in rockets]
        pool = non_bomb if non_bomb else non_pass
        return min(pool, key=self._action_priority)

    @staticmethod
    def _fill_count(action_cards: list[int], card_positions: dict) -> int:
        """按牌型返回游戏补全需手动点的张数(点这些后游戏自动补全剩余)。"""
        from gameauto.skills.doudizhu_douzero.douzero.env.move_detector import get_move_type
        t = get_move_type(action_cards).get("type", 0)
        if t == 8:  # 顺子: 点前2张(连续)触发补全, 如 34→34567
            return 2
        if t == 9:  # 连对: 点前3张触发补全, 如 334→334455
            return 3
        if t in (2, 3):  # 对子/三张: 天然(手牌正好)点1, 拆则全部
            r = EnvCard2RealCard.get(action_cards[0], str(action_cards[0]))
            return 1 if len(card_positions.get(r, [])) == len(action_cards) else len(action_cards)
        # 三带一/三带二/飞机/混合: 不补全, 点全部
        return len(action_cards)

    @staticmethod
    def _action_priority(a: list[int]) -> tuple:
        """出牌优先级: combo_rank 小=优先, 然后点数小、张数少。"""
        from gameauto.skills.doudizhu_douzero.douzero.env.move_detector import get_move_type
        t = get_move_type(a).get("type", 0)
        # 大组合(顺子8/连对9)最优先; 三带(6/7); 三张3; 对子2; 单张1 最后。
        # 飞机(10-12)默认不出(UI 边界未确认), 排到最后; 只剩飞机才出。
        # 炸弹4/王炸5 已在 _rule_select 排除。
        combo_rank = {8: 0, 9: 0, 6: 1, 7: 1, 3: 2, 2: 3, 1: 4,
                      10: 6, 11: 6, 12: 6}.get(t, 5)
        return (combo_rank, max(a), len(a))

    def _prev_position(self) -> str:
        """我前一位(上家, 对应屏幕 play_up 区)。"""
        i = self._ORDER.index(self._my_position)
        return self._ORDER[(i - 1) % 3]

    def _calibrate_env(self, perception: dict) -> None:
        """用屏幕 ground truth 校准 env: 要压的牌 + acting = 我方。

        - 要压 = 上家(play_up)最近出牌; 上家 pass 则下家(play_down)
        - 写入 env 最近一手(让 get_last_move / legal_actions 基于它)
        - last_move_dict[上家] = 该牌
        - acting_player_position = 我方(屏幕[出牌]=轮我)
        """
        if self._env is None or self._my_position is None:
            return
        last_up = perception.get("last_play_up", [])
        last_down = perception.get("last_play_down", [])
        calib = last_up if last_up else last_down
        genv = self._env._env  # GameEnv
        if calib:
            env_cards = [RealCard2EnvCard[c] for c in calib if c in RealCard2EnvCard]
            seq = genv.card_play_action_seq
            if seq:
                seq[-1] = env_cards
            else:
                seq.append(env_cards)
            genv.last_move_dict[self._prev_position()] = env_cards
        else:
            # 自由出牌(屏幕无对手牌): 必须清 last_move —— 否则 legal_actions 仍含 pass,
            # DeepAgent 会选 pass, 但屏幕只有「出牌」没有「不出」点不了 → 死循环
            genv.card_play_action_seq.clear()
        # 校准我方手牌为屏幕当前值(单步模式不 step, env 手牌可能还是开局全量)
        my_hand = perception.get("my_hand", [])
        if my_hand:
            genv.info_sets[self._my_position].player_hand_cards = sorted(
                RealCard2EnvCard[c] for c in my_hand if c in RealCard2EnvCard)
        genv.acting_player_position = self._my_position
        # 重算 legal_actions —— 否则 game_infoset 是旧字段, legal 还是基于开局 last_move 空
        genv.game_infoset = genv.get_infoset()
        self._env.infoset = self._env._game_infoset

    # ------------------------------------------------------------------
    # Opponent turn handling (方案A 下基本不用, 保留兜底)
    # ------------------------------------------------------------------

    def _handle_opponent_turn(
        self, acting_pos: str, perception: dict
    ) -> list[Action]:
        """Record opponent's move from perception and step the env."""
        last_play = perception.get("last_play", [])
        is_pass = perception.get("is_pass", False)

        if not last_play and not is_pass:
            return []  # 模糊帧: 既没牌也没 pass 标志, 不推进, 等下一帧
        try:
            # last_play 优先: 有牌就推进出牌(is_pass 可能误匹配, 牌更可信)
            if last_play:
                self._opponent_play(acting_pos, last_play)
            else:
                self._opponent_pass(acting_pos)
            self._env.infoset = self._env._game_infoset
        except Exception:
            # env 状态偏离(轮次/手牌与屏幕脱节)致出牌校验 assert 失败,
            # 重置 env 避免连锁崩溃; 下帧会重新 init。
            logger.exception("对手出牌推进失败(env 状态偏离), 重置 env")
            self.reset()
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
        """Build tap actions to select cards and press '出牌'.

        UI 边界:
        - 只有「出牌」无「不出」= 首动/自由出 → 需手动点每一张要出的牌。
        - 有「出牌」+「不出」= 压上家牌(≥2张) → 游戏自动补全, 只点一张代表牌即可。
        """
        actions: list[Action] = []
        card_positions = perception.get("card_positions", {})
        # 按牌型决定补全需手动点几张(点起始张数后游戏自动补全剩余); 不补全的牌型点全部。
        # 顺子点前2、连对点前3; 对子/三张天然(手牌正好)点1, 拆则全部; 三带/混合全部。
        fill = self._fill_count(action_cards, card_positions)
        remaining = {k: list(v) for k, v in card_positions.items()}
        cards_to_tap = action_cards[:fill]
        for card in cards_to_tap:
            card_name = EnvCard2RealCard.get(card, str(card))
            slots = remaining.get(card_name)
            if slots:
                pos = slots.pop(0)
                tag = "(自动补全)" if fill < len(action_cards) else ""
                actions.append(
                    Action(
                        type="tap",
                        x1=pos[0],
                        y1=pos[1],
                        description=f"选牌 {card_name}{tag}",
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
        """Pass: 点击「不出/要不起」。

        单步模式不 step env —— env 靠每帧屏幕校准(_calibrate_env), 推进会因
        env 漂移(acting 错位)触发 legal_actions assert。这里只负责点击按钮。
        """
        # Find and tap the "不出" / "要不起" button (both = pass on this round)
        buttons = perception.get("buttons", [])
        for btn in buttons:
            if "不出" in btn.get("text", "") or "要不起" in btn.get("text", ""):
                return [
                    Action(
                        type="tap",
                        x1=btn["x"],
                        y1=btn["y"],
                        description="点击「不出/要不起」",
                    ),
                ]

        logger.warning(
            "No '不出' button found — returning empty action list"
        )
        return []

    def _fallback_pass(self, perception: dict) -> list[Action]:
        """Fallback: pass when DeepAgent fails."""
        logger.warning("DeepAgent failed, falling back to pass")
        return self._action_pass(perception)

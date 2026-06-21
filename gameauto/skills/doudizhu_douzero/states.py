"""DouDiZhu DouZero state registration — multi-state with CV detectors."""

from __future__ import annotations

import asyncio
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
        # 优先级: playing/bidding(buttons ROI 快)优先, 全图导航(lobby/settlement)靠后。
        # 这样轮我方/叫牌帧(最常见)直接命中 buttons ROI, 不查全图; 仅 idle/结算/大厅帧才全图。
        sm.register(PLAYING, detector=self._detect_playing, handler=self._handle)
        sm.register(BIDDING, detector=self._detect_bidding, handler=self._handle)
        sm.register(SETTLEMENT, detector=self._detect_settlement, handler=self._handle)
        sm.register(LOBBY, detector=self._detect_lobby, handler=self._handle)
        # 兜底: 以上都不命中(无按钮/对手思考/过渡帧) → 进 _handle, 由它判无按钮并休眠
        sm.register("idle", detector=lambda _img: True, handler=self._handle)

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
        """分层 handler: 先识别按钮(快), 按需再识别手牌(慢)。

        策略(按钮优先, 层次递进):
          - 一个按钮都没有 → 不在自己轮次(对手思考/动画/过渡帧): 休眠 0.5s, 不识别手牌
          - 要不起/不出 → 直接 pass, 不识别手牌、不调 DeepAgent
          - 叫牌/继续/开始游戏 → 点按钮即可, 不识别手牌
          - 出牌(轮到我方) → 才全量识别手牌 + DeepAgent 决策
        """
        round_dir = self._round_dir(context)

        # ── 第1层: 只识别按钮 + ui 标志(跳过手牌/对手/底牌) ──
        try:
            s1 = (await self._perception.recognize(image, buttons_only=True)).parsed
        except Exception:
            logger.exception("CV perception (buttons) failed")
            return []
        buttons = s1.get("buttons", [])
        names = [b.get("text", "") for b in buttons]
        logger.info("Buttons: %s", names)

        # 无任何按钮 → 不在自己轮次: 休眠, 不识别手牌
        if not buttons:
            await asyncio.sleep(0.5)
            self._save_debug(round_dir, image, s1, [])
            return []

        # 要不起 = 压不住上家(UI 判定), 直接 pass; 不识别手牌、不调 DouZero。
        # 注意: 「不出」+「出牌」同时出现说明有牌能打过, 必须交给 DouZero 决策, 不能直接 pass。
        if any("要不起" in n for n in names):
            logger.info("[UI判定] 压不住 → 点要不起")
            actions = self._decision._action_pass(s1)
            self._save_debug(round_dir, image, s1, actions)
            return actions

        # 叫牌/结算/大厅: 固定策略, 点按钮即可, 不识别手牌
        if any(k in n for n in names for k in ["叫地主", "不叫", "抢地主", "加倍", "不加倍"]):
            actions = self._decision._decide_bidding(buttons)
            logger.info("[固定策略] 叫牌 → %s", [a.description for a in actions])
            self._save_debug(round_dir, image, s1, actions)
            return actions
        if any("继续" in n for n in names):
            actions = self._decision._decide_settlement(buttons)
            logger.info("[固定策略] 结算 → 点继续")
            self._perception.unlock_landmark()  # 新一局, 重新识别 landlord/底牌
            self._save_debug(round_dir, image, s1, actions)
            return actions
        if any("开始游戏" in n for n in names):
            actions = self._decision._decide_lobby(buttons)
            logger.info("[固定策略] 大厅 → 点开始游戏")
            self._perception.unlock_landmark()  # 新一局
            self._save_debug(round_dir, image, s1, actions)
            return actions

        # ── 第2层: 出牌(轮到我方, 有「出牌」按钮) → 全量识别手牌 + DouZero 决策 ──
        # 到这里 buttons 一般是 ['出牌', '不出']: 有牌能打, 由 DouZero 决定出牌还是不出
        logger.info("[DouZero] 出牌决策(全量识别手牌)")
        try:
            result = await self._perception.recognize(image)
        except Exception:
            logger.exception("CV perception (full) failed")
            return []
        state = result.parsed
        # 识别结果先落盘: 决策(DouZero)即便崩溃, 识别也保留供复盘
        self._save_debug(round_dir, image, state, None)

        if not self._decision._round_initialized:
            self._auto_init_round(state)
        actions = self._decision.decide(state)
        if self._decision.is_round_over:
            logger.info("Round %d over, winner: %s", context.round_num, self._decision.winner)
            self._decision.reset()
        # 决策后再补存 actions(覆盖上面写入的空 actions)
        if actions:
            (round_dir / "actions.png").write_bytes(annotate_actions(image, actions))
        (round_dir / "actions.json").write_text(
            json.dumps([{"step": i + 1, "type": a.type, "x": a.x1, "y": a.y1,
                         "description": a.description} for i, a in enumerate(actions)],
                       ensure_ascii=False, indent=2), encoding="utf-8")
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

    def _save_debug(self, round_dir: Path, image: bytes, state: dict,
                    actions: list[Action] | None = None) -> None:
        """Save perception JSON + annotated image + actions(若有)。"""
        (round_dir / "perception.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if state:
            (round_dir / "perception.png").write_bytes(annotate_perception(image, state))
        acts = actions or []
        if acts:
            (round_dir / "actions.png").write_bytes(annotate_actions(image, acts))
        (round_dir / "actions.json").write_text(
            json.dumps([{"step": i + 1, "type": a.type, "x": a.x1, "y": a.y1,
                         "description": a.description} for i, a in enumerate(acts)],
                       ensure_ascii=False, indent=2), encoding="utf-8")

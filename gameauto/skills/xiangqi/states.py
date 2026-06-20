"""Xiangqi state registration — unified handler for 天天象棋."""

from __future__ import annotations

import json
import logging
import time
import traceback
from pathlib import Path

from gameauto.core.orchestration.base import Action, GameState
from gameauto.core.orchestration.context import GameContext
from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.skills.xiangqi.decision import decide
from gameauto.skills.xiangqi.perception import XiangqiPerceptionLike
from gameauto.skills.xiangqi.visualizer import annotate_board_state, annotate_move

logger = logging.getLogger("gameauto.xiangqi")

PLAYING = GameState.PLAYING


class XiangqiStateRegistrar:
    """向状态机注册天天象棋的游戏状态。"""

    def __init__(self, perception: XiangqiPerceptionLike) -> None:
        self._perception = perception
        # 防双走守卫: 我方走完后记录预期棋盘签名, 下一帧若仍一致 = 对手还没走 → 等待。
        self._wait_sig: frozenset | None = None

    def register(self, sm: StateMachine) -> None:
        sm.register(PLAYING, detector=self._always, handler=self._handle)

    def _always(self, image: bytes) -> bool:
        return True

    async def _handle(self, image: bytes, context: GameContext) -> list[Action]:
        # ── VLM 感知 ──────────────────────────────────────────────
        t0 = time.time()
        try:
            result = await self._perception.recognize(image)
        except Exception:
            logger.error("VLM call failed:\n%s", traceback.format_exc())
            return []

        state = result.parsed
        latency = (time.time() - t0) * 1000
        screen_type = state.get("screen_type", "unknown")
        n_pieces = len(state.get("pieces", []))
        logger.info("Perception: %.0fms, screen=%s, pieces=%d", latency, screen_type, n_pieces)

        # ── 保存调试输出 ──────────────────────────────────────────
        round_dir = self._round_dir(context)
        self._save_debug(round_dir, image, state)

        # ── 终止条件: round > 50 ──────────────────────────────────
        if context.round_num > 50:
            logger.info("Max rounds (50) reached — stopping")
            context.max_rounds = context.round_num
            return []

        # ── 单局模式: 检测到 game_over (再来一局界面) → 下完一局, 停 ──
        if screen_type == "game_over":
            logger.info("Game over detected — single game complete, stopping")
            context.max_rounds = context.round_num
            return []

        # ── 防双走守卫: 等对手走子 ────────────────────────────────
        # 我方(红)走完后存了预期棋盘签名; 若当前帧仍一致, 说明对手还没动 → 跳过等待,
        # 避免在我方回合连走两手。签名变化(对手动了)才继续。
        if screen_type == "playing" and self._wait_sig is not None:
            cur_sig = self._board_sig(state.get("pieces", []))
            if cur_sig == self._wait_sig:
                logger.info("Waiting for opponent (board unchanged since our move)")
                return []
            logger.info("Opponent moved — our turn")
            self._wait_sig = None

        # ── 决策 ──────────────────────────────────────────────────
        actions, move = decide(state, context.round_num)
        if actions:
            # 对局走子: 记录我方走完后的预期棋盘签名, 供下一帧守卫判断对手是否已走
            if screen_type == "playing" and move is not None:
                self._wait_sig = self._post_move_sig(state.get("pieces", []), move)
            self._save_move_viz(round_dir, image, state, actions)
        return actions

    @staticmethod
    def _board_sig(pieces: list[dict]) -> frozenset:
        """当前棋盘签名: (col, row, side)。用 side 而非字形, 抗识别抖动。"""
        return frozenset(
            (p["board_pos"]["col"], p["board_pos"]["row"], p.get("side"))
            for p in pieces
        )

    @staticmethod
    def _post_move_sig(pieces: list[dict], move: dict) -> frozenset:
        """把走法应用到当前棋子集, 得到我方走完后的预期棋盘签名。

        用于下一帧判断对手是否已动: 走子→移动棋子到目标格(吃子则覆盖);
        对手若没动, 真实棋盘签名应与此一致。
        """
        occ = {
            (p["board_pos"]["col"], p["board_pos"]["row"]): p.get("side")
            for p in pieces
        }
        f = (move["from"]["col"], move["from"]["row"])
        t = (move["to"]["col"], move["to"]["row"])
        if f in occ:
            occ[t] = occ.pop(f)  # 我方棋子移到目标格, 覆盖被吃棋子
        return frozenset((c, r, s) for (c, r), s in occ.items())

    def _round_dir(self, context: GameContext) -> Path:
        d = context.session_dir / f"round_{context.round_num:03d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_debug(self, round_dir: Path, image: bytes, state: dict) -> None:
        (round_dir / "vlm_response.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        logger.info("VLM response:\n%s", json.dumps(state, ensure_ascii=False, indent=2))
        if state.get("pieces"):
            annotated = annotate_board_state(image, state)
            (round_dir / "perception.png").write_bytes(annotated)

    def _save_move_viz(self, round_dir: Path, image: bytes, state: dict, actions: list[Action]) -> None:
        # 提取点击坐标用于可视化
        taps = [(a.x1, a.y1, a.description or "") for a in actions if a.type == "tap"]
        if len(taps) >= 2:
            move_img = annotate_move(image, state, taps)
            (round_dir / "move.png").write_bytes(move_img)
        if actions:
            from gameauto.skills.xiangqi.visualizer import annotate_clicks
            (round_dir / "clicks.png").write_bytes(annotate_clicks(image, actions))

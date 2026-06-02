"""Match-3 状态注册 — 每个游戏状态的 detector + handler。

detector: 判断当前画面是否处于某个状态（看截图，返回 bool）
handler:  在该状态下应该做什么（感知 + 决策，返回 Action[]）

M1 只注册 IN_GAME 一个状态。新增游戏时可以注册更多状态，
例如金铲铲需要 LOBBY, QUEUE, PLANNING, COMBAT, RESULT 等。
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from pathlib import Path

from gameauto.core.orchestration.base import Action, GameState
from gameauto.core.orchestration.context import GameContext
from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.skills.match3.perception import Match3Perception
from gameauto.skills.match3.solver import solve_board_multi
from gameauto.skills.match3.visualizer import annotate_board, annotate_multi_swipe, annotate_swipe
from gameauto.utils.images import image_dimensions

logger = logging.getLogger("gameauto.match3")


class Match3StateRegistrar:
    """向状态机注册消消乐的游戏状态。

    每个状态 = detector(截图) → bool + handler(截图, 上下文) → Action[]

    用法:
        perception = Match3Perception(vlm, prompt)
        registrar = Match3StateRegistrar(perception, max_steps=2)
        registrar.register(state_machine)
    """

    def __init__(self, perception: Match3Perception, max_steps: int = 1) -> None:
        self._perception = perception
        self._max_steps = max_steps

    def register(self, sm: StateMachine) -> None:
        """向 StateMachine 注册所有消消乐状态（M1: 仅 IN_GAME）。"""
        sm.register(
            GameState.IN_GAME,
            detector=self._is_in_game,
            handler=self._handle_in_game,
        )

    # ── Detector ──────────────────────────────────────────────────────

    def _is_in_game(self, image: bytes) -> bool:
        """M1: 总是判定为游戏中。后续可加模板匹配检测棋盘 UI。"""
        return True

    # ── Handler ───────────────────────────────────────────────────────

    def _round_dir(self, context: GameContext) -> Path:
        """获取本轮日志目录，自动创建。"""
        d = context.session_dir / f"round_{context.round_num:03d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def _handle_in_game(self, image: bytes, context: GameContext) -> list[Action]:
        """消消乐核心处理逻辑 — 每轮调用一次。

        流程:
          1. Perceive: VLM 识别棋盘 → board JSON
          2. Visualize: 保存棋盘网格覆盖图（调试用）
          3. Solve: 贪心求解 → N 个 swap（N = max_steps）
          4. Visualize: 保存滑动箭头图（调试用）
          5. Return: Action[] 交给 GameLoop 执行

        Returns:
            Action 列表，空列表表示本轮无有效操作。
        """
        # ── 1. Perceive: VLM 识别棋盘 ─────────────────────────────
        t0 = time.time()
        try:
            result = await self._perception.recognize(image)
        except Exception:
            logger.error("VLM call failed:\n%s", traceback.format_exc())
            return []
        board = result.parsed
        latency = (time.time() - t0) * 1000
        logger.info("Perception: %.0fms, board %sx%s", latency,
                     board.get("rows", "?"), board.get("cols", "?"))

        if not board or "tiles" not in board:
            logger.warning("Failed to recognize board")
            return []

        # ── 2. Save raw VLM response + board data ─────────────────
        round_dir = self._round_dir(context)
        # 保存 VLM 原始输出（调试用）
        (round_dir / "vlm_response.txt").write_text(
            result.raw_response or "", encoding="utf-8",
        )
        # 打印 VLM 原始输出（控制台可见，便于实时观察识别质量）
        logger.info("VLM response:\n%s", result.raw_response or "(empty)")
        # 保存解析后的棋盘 JSON + 可视化
        # board.json + board.png 用于离线调试和回灌
        # board.json + board.png 用于离线调试和回灌
        round_dir = self._round_dir(context)
        (round_dir / "board.json").write_text(
            json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        board_img = annotate_board(image, board)
        (round_dir / "board.png").write_bytes(board_img)

        # ── 3. Solve: 贪心求解（支持多步独立交换） ────────────────
        swaps = solve_board_multi(board, max_steps=self._max_steps)
        logger.info("Solver: %d swap(s) found", len(swaps))

        if not swaps:
            logger.warning("No valid swaps on current board")
            return []

        # ── 4. Convert to Actions + compute swipe coords ──────────
        # 坐标两套: 归一化 [0-1000] 给 Input 执行，像素坐标给可视化
        native_w, native_h = image_dimensions(image)
        actions = []
        swipe_coords = []

        for swap in swaps:
            coords = swap["coordinates"]
            fr = coords["from"]   # 归一化 [x, y]
            to = coords["to"]
            actions.append(Action(
                type="swipe",
                x1=fr[0], y1=fr[1],
                x2=to[0], y2=to[1],
                duration_ms=1000,
                description=swap.get("match_description", ""),
            ))
            # 像素坐标用于可视化标注
            x1 = int(fr[0] * native_w / 1000)
            y1 = int(fr[1] * native_h / 1000)
            x2 = int(to[0] * native_w / 1000)
            y2 = int(to[1] * native_h / 1000)
            swipe_coords.append((x1, y1, x2, y2))

        # ── 5. Save swipe visualization ──────────────────────────
        # 单步用红色箭头，多步用不同颜色区分
        if len(swipe_coords) == 1:
            swipe_img = annotate_swipe(image, *swipe_coords[0])
        else:
            swipe_img = annotate_multi_swipe(image, swipe_coords)
        (round_dir / "swipe.png").write_bytes(swipe_img)
        logger.debug("Visualization saved: %s", round_dir)

        return actions

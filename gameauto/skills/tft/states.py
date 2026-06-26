"""TFT 状态注册 — 5 个游戏状态的 detector + 统一分层 handler。

detector: 同步函数，用模板匹配判断当前画面状态（< 30ms）
handler:  统一的异步分层 handler，按状态决定是否运行全量 OCR

状态转换:
    LOBBY → QUEUE_LOADING → PLANNING ⇄ COMBAT → RESULT → LOBBY

分层策略（参考斗地主 doudizhu_douzero/states.py）:
    - LOBBY / COMBAT / RESULT / LOADING / idle → 不调 OCR，直接操作或 sleep
    - PLANNING → 全量 OCR（金币/血量/等级/计时器/5 商店 slot）→ L1 规则决策
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
from pathlib import Path
from typing import Any

from gameauto.core.orchestration.base import Action
from gameauto.core.orchestration.context import GameContext
from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.core.perception.cv.template_match import TemplateMatchTask
from gameauto.skills.tft.decision import TftDecision
from gameauto.skills.tft.perception import TftPerception
from gameauto.utils.images import image_dimensions

logger = logging.getLogger("gameauto.tft.states")


# ── Game state constants ───────────────────────────────────────────────

class TftState:
    """TFT game states — plain strings following GameState pattern."""
    LOBBY = "tft_lobby"
    QUEUE_LOADING = "tft_queue_loading"
    PLANNING = "tft_planning"
    COMBAT = "tft_combat"
    RESULT = "tft_result"  # 结算画面（排名显示）

    # Ordered list for state detection priority
    ALL = [LOBBY, PLANNING, COMBAT, RESULT, QUEUE_LOADING]


# ── StateRegistrar ─────────────────────────────────────────────────────

class TftStateRegistrar:
    """向 StateMachine 注册金铲铲的 5 个游戏状态 + idle 兜底。

    参考斗地主 DouZero 的分层 handler 模式:
    - 所有状态共享一个 _handle 方法
    - detector 只做路由，handler 内部按 context.state 分支
    - 只有 PLANNING 状态才运行全量 OCR，其他状态直接 sleep 或固定操作

    用法:
        registrar = TftStateRegistrar(perception, decision, tm_task)
        registrar.register(state_machine)
    """

    def __init__(
        self,
        perception: TftPerception,
        decision: TftDecision,
        tm_task: TemplateMatchTask,
    ) -> None:
        self._perception = perception
        self._decision = decision
        self._tm = tm_task

        # Loading timeout tracking
        self._loading_start: float | None = None
        self._loading_timeout_sec: float = 300.0  # 5 minutes

        # Combat check counter (avoid false negatives)
        self._consecutive_combat_checks: int = 0

    def set_loading_timeout(self, seconds: float) -> None:
        """Set the maximum time to stay in QUEUE_LOADING before trying to cancel."""
        self._loading_timeout_sec = seconds

    def register(self, sm: StateMachine) -> None:
        """Register all TFT states + idle fallback.

        所有状态共享同一个 _handle handler，detector 仅做路由。
        Registration order determines detection priority — first match wins.
        """
        sm.register(
            TftState.LOBBY,
            detector=self._is_lobby,
            handler=self._handle,
        )
        sm.register(
            TftState.PLANNING,
            detector=self._is_planning,
            handler=self._handle,
        )
        sm.register(
            TftState.COMBAT,
            detector=self._is_combat,
            handler=self._handle,
        )
        sm.register(
            TftState.RESULT,
            detector=self._is_result,
            handler=self._handle,
        )
        sm.register(
            TftState.QUEUE_LOADING,
            detector=self._is_loading,
            handler=self._handle,
        )
        # 兜底: 以上都不命中(过渡帧/动画/未知画面) → 进 _handle, sleep 0.5s
        sm.register(
            "idle",
            detector=lambda _img: True,
            handler=self._handle,
        )

    # ── Detectors (同步, 轻量模板匹配 < 30ms) ────────────────────────

    def _is_lobby(self, image: bytes) -> bool:
        """Detect the main lobby screen (play button visible)."""
        roi = self._perception.get_state_detection_roi("lobby_play_btn")
        return self._tm._match_inline(
            image, roi, "lobby_play_btn", threshold=0.72,
        )

    def _is_planning(self, image: bytes) -> bool:
        """Detect the planning/preparation phase (round timer visible)."""
        roi = self._perception.get_state_detection_roi("planning_timer")
        return self._tm._match_inline(
            image, roi, "planning_timer", threshold=0.72,
        )

    def _is_combat(self, image: bytes) -> bool:
        """Detect the combat phase (fighting indicator visible).

        Uses template match + fallback: if planning NOT detected for 2+
        consecutive frames, assume combat.
        """
        roi = self._perception.get_state_detection_roi("combat_indicator")
        direct = self._tm._match_inline(
            image, roi, "combat_indicator", threshold=0.72,
        )
        if direct:
            self._consecutive_combat_checks = 0
            return True

        # Fallback: if planning timer is ALSO not visible, likely combat
        planning_roi = self._perception.get_state_detection_roi("planning_timer")
        is_planning = self._tm._match_inline(
            image, planning_roi, "planning_timer", threshold=0.65,
        )

        if not is_planning:
            self._consecutive_combat_checks += 1
            if self._consecutive_combat_checks >= 2:
                return True
        else:
            self._consecutive_combat_checks = 0

        return False

    def _is_result(self, image: bytes) -> bool:
        """Detect the result/settlement screen (rank display visible)."""
        roi = self._perception.get_state_detection_roi("result_rank")
        return self._tm._match_inline(
            image, roi, "result_rank", threshold=0.72,
        )

    def _is_loading(self, image: bytes) -> bool:
        """Detect queue/loading screen.

        Uses exclusion: if lobby/planning/combat/result are all NOT detected,
        it's loading/transition.
        """
        # Check if any other state is a better match first
        lobby_roi = self._perception.get_state_detection_roi("lobby_play_btn")
        planning_roi = self._perception.get_state_detection_roi("planning_timer")
        combat_roi = self._perception.get_state_detection_roi("combat_indicator")
        result_roi = self._perception.get_state_detection_roi("result_rank")

        is_lobby = self._tm._match_inline(
            image, lobby_roi, "lobby_play_btn", threshold=0.72,
        )
        is_planning = self._tm._match_inline(
            image, planning_roi, "planning_timer", threshold=0.65,
        )
        is_combat = self._tm._match_inline(
            image, combat_roi, "combat_indicator", threshold=0.72,
        )
        is_result = self._tm._match_inline(
            image, result_roi, "result_rank", threshold=0.72,
        )

        # If any other state matches, we're NOT loading
        if is_lobby or is_planning or is_combat or is_result:
            self._loading_start = None
            return False

        # Try direct loading indicator match
        loading_roi = self._perception.get_state_detection_roi("loading_indicator")
        direct = self._tm._match_inline(
            image, loading_roi, "loading_indicator", threshold=0.60,
        )
        if direct:
            return True

        # Fallback: none of the known states matched → assume loading/transition
        return True

    # ── Unified layered handler ────────────────────────────────────────

    async def _handle(self, image: bytes, context: GameContext) -> list[Action]:
        """统一分层 handler: 按 context.state 决定操作深度。

        参考斗地主模式:
        - PLANNING:  全量 OCR + L1 决策(唯一重型操作)
        - COMBAT:    sleep 2s, 不调 OCR
        - LOBBY:     点开始游戏 + wait 6s
        - RESULT:    点返回大厅 + wait 3s
        - LOADING:   wait 3s(超时退出)
        - idle:      sleep 0.5s
        """
        state = context.state

        if state == TftState.PLANNING:
            return await self._handle_planning(image, context)
        elif state == TftState.COMBAT:
            return await self._handle_combat(image, context)
        elif state == TftState.LOBBY:
            return await self._handle_lobby(image, context)
        elif state == TftState.RESULT:
            return await self._handle_result(image, context)
        elif state == TftState.QUEUE_LOADING:
            return await self._handle_loading(image, context)
        else:
            # idle / unknown — sleep 0.5s, no actions
            await asyncio.sleep(0.5)
            return []

    # ── Per-state handlers ─────────────────────────────────────────────

    async def _handle_planning(
        self, image: bytes, context: GameContext,
    ) -> list[Action]:
        """PLANNING: 核心游戏状态 — 全量 OCR + L1 规则决策。

        Flow:
            1. 全量 OCR（金币/血量/等级/计时器/商店/备战席）
            2. 识别结果先落盘（决策崩也保留）
            3. L1 规则引擎决策
            4. 返回 Actions
        """
        self._loading_start = None  # Clear loading timer
        self._consecutive_combat_checks = 0
        round_dir = self._round_dir(context)

        # ── 1. 全量 OCR ──────────────────────────────────────────────
        t0 = time.time()
        try:
            result = await self._perception.recognize(image)
        except Exception:
            logger.error("Perception failed:\n%s", traceback.format_exc())
            return []

        perception = result.parsed
        latency = (time.time() - t0) * 1000

        # ── 2. 识别结果先落盘（斗地主教训: 决策崩也保留识别数据）──────
        self._save_debug(round_dir, image, result, perception, latency)

        # ── 3. Log perception summary ────────────────────────────────
        shop_names = [u or "-" for u in perception.get("shop_units", [])]
        gray_slots = perception.get("gray_slots", [])
        logger.info(
            "[PLANNING] perception %.0fms | gold=%s lv=%s hp=%s timer=%s "
            "| shop=[%s] gray=%s",
            latency,
            perception.get("gold", "?"),
            perception.get("level", "?"),
            perception.get("hp", "?"),
            perception.get("timer_seconds", "?"),
            ", ".join(shop_names),
            gray_slots,
        )

        # ── 4. L1 决策（识别已落盘, 崩了也不丢数据）──────────────────
        try:
            actions = self._decision.decide(perception, context)
        except Exception:
            logger.error("Decision failed:\n%s", traceback.format_exc())
            return []

        # 决策后再补存 actions
        if actions:
            (round_dir / "actions.json").write_text(
                json.dumps(
                    [
                        {
                            "step": i + 1,
                            "type": a.type,
                            "x": a.x1,
                            "y": a.y1,
                            "description": a.description,
                        }
                        for i, a in enumerate(actions)
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        return actions

    async def _handle_combat(
        self, image: bytes, context: GameContext,
    ) -> list[Action]:
        """COMBAT: 等待战斗结束，不调 OCR，省时间。"""
        logger.debug("[COMBAT] Waiting for next round...")
        self._loading_start = None
        self._consecutive_combat_checks = 0
        await asyncio.sleep(2.0)
        return []

    async def _handle_lobby(
        self, image: bytes, context: GameContext,
    ) -> list[Action]:
        """LOBBY: 点开始游戏按钮 + 等待 6s 加载。"""
        logger.info("[LOBBY] Starting match search")
        self._decision.reset()

        # Tap start button (center of lobby play button ROI)
        roi = self._perception.get_state_detection_roi("lobby_play_btn")
        left, top, right, bottom = roi

        # Center of ROI, normalized to [0-1000]
        cx = int((left + right) / 2 * 1000)
        cy = int((top + bottom) / 2 * 1000)

        actions = [Action(
            type="tap",
            x1=cx, y1=cy,
            duration_ms=150,  # HOS 最小 150ms（斗地主验证）
            description="Tap play button → queue",
        )]

        # 点击后等动画加载（斗地主经验: 开始游戏需 wait 6s）
        await asyncio.sleep(6.0)
        return actions

    async def _handle_result(
        self, image: bytes, context: GameContext,
    ) -> list[Action]:
        """RESULT: 结算画面 — 点返回大厅 + 等待 3s。"""
        logger.info("[RESULT] Match ended, returning to lobby")
        self._decision.reset()

        # Tap "return to lobby" button (center-bottom area, typical for TFT)
        # 结算界面返回按钮一般在中间偏下
        actions = [Action(
            type="tap",
            x1=500, y1=900,
            duration_ms=150,
            description="Tap return to lobby",
        )]

        await asyncio.sleep(3.0)
        return actions

    async def _handle_loading(
        self, image: bytes, context: GameContext,
    ) -> list[Action]:
        """QUEUE_LOADING: 等待游戏开始。

        If stuck for > loading_timeout, tap back to cancel and return to lobby.
        """
        now = time.time()

        if self._loading_start is None:
            self._loading_start = now
            elapsed = 0.0
        else:
            elapsed = now - self._loading_start

        # Check timeout
        if elapsed > self._loading_timeout_sec:
            logger.warning(
                "[QUEUE_LOADING] Timeout after %.0fs — tapping back", elapsed,
            )
            self._loading_start = None
            return [Action(
                type="tap",
                x1=30, y1=30,  # Top-left back button
                duration_ms=150,
                description="Timeout cancel — tap back",
            )]

        logger.debug("[QUEUE_LOADING] Waiting... (%.0fs)", elapsed)
        await asyncio.sleep(3.0)
        return []

    # ── Helpers ────────────────────────────────────────────────────────

    def _round_dir(self, context: GameContext) -> Path:
        """Get/create the round's log directory."""
        d = context.session_dir / f"round_{context.round_num:03d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_debug(
        self,
        round_dir: Path,
        image: bytes,
        result: Any,
        perception: dict[str, Any],
        latency_ms: float,
    ) -> None:
        """Save perception debug data to round directory.

        先于 decide 调用 — 决策崩溃也保留识别数据（斗地主教训）。
        """
        try:
            # Save raw screenshot
            (round_dir / "screenshot.png").write_bytes(image)

            # Save perception result as JSON
            debug_data = {
                "latency_ms": latency_ms,
                "gold": perception.get("gold"),
                "level": perception.get("level"),
                "hp": perception.get("hp"),
                "timer_seconds": perception.get("timer_seconds"),
                "shop_units": perception.get("shop_units"),
                "gray_slots": perception.get("gray_slots"),
                "bench_text": perception.get("bench_text"),
                "stage_text": perception.get("stage_text"),
                "raw_tasks": {
                    k: v for k, v in (result.tasks_output or {}).items()
                    if isinstance(v, dict)
                },
            }
            (round_dir / "perception.json").write_text(
                json.dumps(debug_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            logger.debug("Debug saved: %s", round_dir)
        except Exception:
            logger.warning("Failed to save debug data: %s", traceback.format_exc())

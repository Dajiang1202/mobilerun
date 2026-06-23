"""TFT 状态注册 — 4 个游戏状态的 detector + handler。

detector: 同步函数，用模板匹配判断当前画面状态（< 30ms）
handler:  异步函数，感知 + 决策 → Action[]

状态转换:
    LOBBY → QUEUE_LOADING → PLANNING ⇄ COMBAT → (RESULT → LOBBY)
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

    # Ordered list for state detection priority
    ALL = [LOBBY, PLANNING, COMBAT, QUEUE_LOADING]


# ── StateRegistrar ─────────────────────────────────────────────────────

class TftStateRegistrar:
    """向 StateMachine 注册金铲铲的 4 个游戏状态。

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
        """Register all TFT states.

        Registration order determines detection priority — first match wins.
        """
        sm.register(
            TftState.LOBBY,
            detector=self._is_lobby,
            handler=self._handle_lobby,
        )
        sm.register(
            TftState.PLANNING,
            detector=self._is_planning,
            handler=self._handle_planning,
        )
        sm.register(
            TftState.COMBAT,
            detector=self._is_combat,
            handler=self._handle_combat,
        )
        sm.register(
            TftState.QUEUE_LOADING,
            detector=self._is_loading,
            handler=self._handle_loading,
        )

    # ── Detectors ──────────────────────────────────────────────────────

    def _is_lobby(self, image: bytes) -> bool:
        """Detect the main lobby screen (play button visible)."""
        roi = self._perception.get_state_detection_roi("lobby_play_btn")
        result = self._tm._match_inline(image, roi, "lobby_play_btn", threshold=0.70)
        return result

    def _is_planning(self, image: bytes) -> bool:
        """Detect the planning/preparation phase (round timer visible)."""
        roi = self._perception.get_state_detection_roi("planning_timer")
        result = self._tm._match_inline(image, roi, "planning_timer", threshold=0.70)
        return result

    def _is_combat(self, image: bytes) -> bool:
        """Detect the combat phase (fighting indicator visible).

        Uses template match + fallback: if planning NOT detected for 2+
        consecutive frames, assume combat.
        """
        roi = self._perception.get_state_detection_roi("combat_indicator")
        direct = self._tm._match_inline(image, roi, "combat_indicator", threshold=0.70)
        if direct:
            self._consecutive_combat_checks = 0
            return True

        # Fallback: if planning timer is ALSO not visible, likely combat
        planning_roi = self._perception.get_state_detection_roi("planning_timer")
        is_planning = self._tm._match_inline(image, planning_roi, "planning_timer", threshold=0.65)

        if not is_planning:
            self._consecutive_combat_checks += 1
            if self._consecutive_combat_checks >= 2:
                return True
        else:
            self._consecutive_combat_checks = 0

        return False

    def _is_loading(self, image: bytes) -> bool:
        """Detect queue/loading screen.

        Uses template match + static-screen fallback.
        Returns True if lobby/planning/combat are all NOT detected.
        """
        # Check if any other state is a better match first
        lobby_roi = self._perception.get_state_detection_roi("lobby_play_btn")
        planning_roi = self._perception.get_state_detection_roi("planning_timer")
        combat_roi = self._perception.get_state_detection_roi("combat_indicator")

        is_lobby = self._tm._match_inline(image, lobby_roi, "lobby_play_btn", threshold=0.70)
        is_planning = self._tm._match_inline(image, planning_roi, "planning_timer", threshold=0.65)
        is_combat = self._tm._match_inline(image, combat_roi, "combat_indicator", threshold=0.70)

        # If any other state matches, we're NOT loading
        if is_lobby or is_planning or is_combat:
            self._loading_start = None
            return False

        # Try direct loading indicator match
        loading_roi = self._perception.get_state_detection_roi("loading_indicator")
        direct = self._tm._match_inline(image, loading_roi, "loading_indicator", threshold=0.60)
        if direct:
            return True

        # Fallback: none of the 3 known states matched → assume loading/transition
        return True

    # ── Handlers ───────────────────────────────────────────────────────

    async def _handle_lobby(self, image: bytes, context: GameContext) -> list[Action]:
        """LOBBY: tap the play button to start queueing."""
        logger.info("[LOBBY] Starting match search")
        self._decision.reset()

        # Tap start button (center of lobby play button ROI)
        roi = self._perception.get_state_detection_roi("lobby_play_btn")
        left, top, right, bottom = roi
        native_w, native_h = image_dimensions(image)

        # Center of ROI, normalized to [0-1000]
        cx = int((left + right) / 2 * 1000)
        cy = int((top + bottom) / 2 * 1000)

        return [Action(
            type="tap",
            x1=cx, y1=cy,
            duration_ms=100,
            description="Tap play button → queue",
        )]

    async def _handle_loading(self, image: bytes, context: GameContext) -> list[Action]:
        """QUEUE_LOADING: wait for game to start.

        If stuck for > loading_timeout, try tapping back to cancel and return to lobby.
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
                "[QUEUE_LOADING] Timeout after %.0fs — tapping back", elapsed
            )
            self._loading_start = None
            native_w, native_h = image_dimensions(image)
            return [Action(
                type="tap",
                x1=30, y1=30,  # Top-left back button
                duration_ms=100,
                description="Timeout cancel — tap back",
            )]

        logger.debug("[QUEUE_LOADING] Waiting... (%.0fs)", elapsed)
        await asyncio.sleep(3.0)
        return []  # No action, just waited

    async def _handle_planning(self, image: bytes, context: GameContext) -> list[Action]:
        """PLANNING: the core game state — perceive, decide, act.

        Flow:
            1. Run perception pipeline (OCR gold/level/hp/timer/shop/bench)
            2. Save debug data
            3. Run L1 decision engine
            4. Return Actions
        """
        self._loading_start = None  # Clear loading timer
        round_dir = self._round_dir(context)

        # ── 1. Perceive ────────────────────────────────────────────────
        t0 = time.time()
        try:
            result = await self._perception.recognize(image)
        except Exception:
            logger.error("Perception failed:\n%s", traceback.format_exc())
            return []

        perception = result.parsed
        latency = (time.time() - t0) * 1000

        # ── 2. Save debug data ─────────────────────────────────────────
        self._save_debug(round_dir, image, result, perception, latency)

        # ── 3. Log perception summary ──────────────────────────────────
        shop_names = [u or "-" for u in perception.get("shop_units", [])]
        logger.info(
            "[PLANNING] perception %.0fms | gold=%s lv=%s hp=%s timer=%s | shop=[%s]",
            latency,
            perception.get("gold", "?"),
            perception.get("level", "?"),
            perception.get("hp", "?"),
            perception.get("timer_seconds", "?"),
            ", ".join(shop_names),
        )

        # ── 4. Decide ──────────────────────────────────────────────────
        try:
            actions = self._decision.decide(perception, context)
        except Exception:
            logger.error("Decision failed:\n%s", traceback.format_exc())
            return []

        return actions

    async def _handle_combat(self, image: bytes, context: GameContext) -> list[Action]:
        """COMBAT: wait for the fight to end and next planning phase."""
        logger.debug("[COMBAT] Waiting for next round...")
        self._loading_start = None  # Clear loading timer
        self._consecutive_combat_checks = 0
        await asyncio.sleep(2.0)
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
        """Save perception debug data to round directory."""
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
                "bench_text": perception.get("bench_text"),
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

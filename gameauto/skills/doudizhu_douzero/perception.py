"""DouDiZhu DouZero CV perception — template matching for cards, buttons, UI.

Uses PerceptionPipeline with parallel TemplateMatchTask instances.
No VLM dependency — all recognition is OpenCV-based.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from gameauto.core.perception.base import PerceptionResult, PerceptionTask
from gameauto.core.perception.cv.template_match import TemplateMatchTask
from gameauto.core.perception.pipeline import PerceptionPipeline

logger = logging.getLogger("gameauto.doudizhu_douzero")


class DouDiZhuDouzeroPerception:
    """CV-based perception for DouDiZhu using template matching.

    Detects: hand cards, opponent plays, landlord markers,
    bidding buttons, play buttons, settlement buttons.

    Usage:
        perception = DouDiZhuDouzeroPerception(
            template_dir="skills/doudizhu_douzero/assets/templates",
        )
        result = await perception.recognize(image_bytes)
    """

    def __init__(
        self,
        template_dir: str,
        card_confidence: float = 0.85,
        button_confidence: float = 0.90,
        pass_confidence: float = 0.90,
    ) -> None:
        self._template_dir = Path(template_dir)
        self._card_confidence = card_confidence
        self._button_confidence = button_confidence
        self._pass_confidence = pass_confidence

        # Template matchers for different regions
        self._card_matcher: TemplateMatchTask | None = None
        self._other_card_matcher: TemplateMatchTask | None = None
        self._button_matcher: TemplateMatchTask | None = None
        self._ui_matcher: TemplateMatchTask | None = None

        self._init_matchers()

    def _init_matchers(self) -> None:
        """Load templates from subdirectories."""
        cards_dir = self._template_dir / "cards"
        others_dir = self._template_dir / "others"
        buttons_dir = self._template_dir / "buttons"
        ui_dir = self._template_dir / "ui"

        if cards_dir.is_dir():
            self._card_matcher = TemplateMatchTask(str(cards_dir))
        if others_dir.is_dir():
            self._other_card_matcher = TemplateMatchTask(str(others_dir))
        if buttons_dir.is_dir():
            self._button_matcher = TemplateMatchTask(str(buttons_dir))
        if ui_dir.is_dir():
            self._ui_matcher = TemplateMatchTask(str(ui_dir))

        loaded = sum(
            1
            for m in [
                self._card_matcher,
                self._other_card_matcher,
                self._button_matcher,
                self._ui_matcher,
            ]
            if m is not None
        )
        logger.info("CV perception initialized: %d template sets loaded", loaded)

    async def recognize(self, image: bytes) -> PerceptionResult:
        """Run all CV tasks in parallel, return structured perception result."""
        pipeline = PerceptionPipeline(vlm_client=None)

        # Task 1: Hand cards (bottom region)
        if self._card_matcher:
            pipeline.add_task(
                PerceptionTask(
                    name="hand_cards",
                    task_type="template_match",
                    roi=(0.0, 0.75, 1.0, 1.0),
                    config={"threshold": self._card_confidence},
                )
            )

        # Task 2: Other players' cards (center region)
        if self._other_card_matcher:
            pipeline.add_task(
                PerceptionTask(
                    name="other_cards",
                    task_type="template_match_other",
                    roi=(0.1, 0.3, 0.9, 0.7),
                    config={"threshold": self._card_confidence},
                )
            )

        # Task 3: Buttons (bottom-right region)
        if self._button_matcher:
            pipeline.add_task(
                PerceptionTask(
                    name="buttons",
                    task_type="template_match_buttons",
                    roi=(0.55, 0.70, 1.0, 1.0),
                    config={"threshold": self._button_confidence},
                )
            )

        # Task 4: UI markers (full screen)
        if self._ui_matcher:
            pipeline.add_task(
                PerceptionTask(
                    name="ui_markers",
                    task_type="template_match_ui",
                    roi=None,
                    config={"threshold": self._button_confidence},
                )
            )

        # Register custom runners for each task type
        pipeline.register_custom_runner(
            "template_match",
            lambda img, task: self._run_matcher(self._card_matcher, img, task),
        )
        pipeline.register_custom_runner(
            "template_match_other",
            lambda img, task: self._run_matcher(self._other_card_matcher, img, task),
        )
        pipeline.register_custom_runner(
            "template_match_buttons",
            lambda img, task: self._run_matcher(self._button_matcher, img, task),
        )
        pipeline.register_custom_runner(
            "template_match_ui",
            lambda img, task: self._run_matcher(self._ui_matcher, img, task),
        )

        result = await pipeline.run(image)
        result.parsed = self._parse_results(result)
        return result

    async def _run_matcher(
        self,
        matcher: TemplateMatchTask | None,
        image: bytes,
        task: PerceptionTask,
    ) -> dict[str, Any]:
        """Run a single template matcher with the task's ROI and config."""
        if matcher is None:
            logger.warning("Matcher not loaded for task '%s'", task.name)
            return {"matches": [], "best_match": None, "best_score": 0.0}
        return await matcher.run(image, roi=task.roi, config=task.config)

    def _parse_results(self, result: PerceptionResult) -> dict[str, Any]:
        """Convert raw template match results to structured perception dict."""
        tasks = result.tasks_output

        # Parse hand cards
        hand_cards_raw = tasks.get("hand_cards", {}) or {}
        hand_cards = []
        card_positions = {}
        if hand_cards_raw and "matches" in hand_cards_raw:
            hand_cards, card_positions = self._parse_card_matches(
                hand_cards_raw["matches"]
            )

        # Parse other cards
        other_cards_raw = tasks.get("other_cards", {}) or {}
        last_play = []
        if other_cards_raw and "matches" in other_cards_raw:
            last_play, _ = self._parse_card_matches(other_cards_raw["matches"])

        # Parse buttons
        buttons_raw = tasks.get("buttons", {}) or {}
        buttons = []
        if buttons_raw and "matches" in buttons_raw:
            for m in buttons_raw["matches"]:
                buttons.append(
                    {
                        "text": m["template"],
                        "x": m["x"] + m["w"] // 2,
                        "y": m["y"] + m["h"] // 2,
                    }
                )

        # Parse UI markers
        ui_raw = tasks.get("ui_markers", {}) or {}
        ui_markers = {}
        if ui_raw and "matches" in ui_raw:
            for m in ui_raw["matches"]:
                ui_markers[m["template"]] = True

        # Determine phase
        button_names = [b["text"] for b in buttons]
        phase = "playing"
        if any(
            name in n for n in button_names for name in ["叫地主", "抢地主", "加倍", "不加倍"]
        ):
            phase = "bidding"
        elif any("继续" in n for n in button_names):
            phase = "settlement"

        return {
            "phase": phase,
            "my_hand": hand_cards,
            "last_play": last_play,
            "is_pass": ui_markers.get("pass", False),
            "is_landlord": ui_markers.get("landlord", False),
            "buttons": buttons,
            "button_names": button_names,
            "card_positions": card_positions,
        }

    @staticmethod
    def _parse_card_matches(
        matches: list[dict],
    ) -> tuple[list[str], dict[str, tuple[int, int]]]:
        """Convert match results to card names and positions."""
        cards = []
        positions = {}
        for m in matches:
            name = m["template"]
            cx = m["x"] + m["w"] // 2
            cy = m["y"] + m["h"] // 2
            cards.append(name)
            positions[name] = (cx, cy)
        return cards, positions

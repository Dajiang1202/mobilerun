"""DouDiZhu perception — VLM-based screen recognition.

Recognizes two game phases:
  - bidding (叫地主/抢地主/不叫/不加倍): buttons + hand cards
  - playing (出牌/不出/提示): buttons + hand cards
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from gameauto.core.perception.base import PerceptionResult
from gameauto.core.perception.vlm_client import VlmClient

logger = logging.getLogger("gameauto.doudizhu.perception")

JSON_RE = re.compile(r"\{[\s\S]*\}")


class DouDiZhuPerception:
    """VLM-based screen recognition for Dou Di Zhu.

    Usage:
        vlm = VlmClient(model="qwen3-vl-flash", ...)
        perception = DouDiZhuPerception.from_prompt_file(vlm, "prompts/doudizhu.jinja2")
        result = await perception.recognize(screenshot)
        # result.parsed = {"phase": "bidding", "buttons": [...], "hand_cards": [...]}
    """

    def __init__(self, vlm: VlmClient, prompt_template: str) -> None:
        self._vlm = vlm
        self._prompt = prompt_template

    @classmethod
    def from_prompt_file(cls, vlm: VlmClient, prompt_path: str) -> "DouDiZhuPerception":
        text = Path(prompt_path).read_text(encoding="utf-8")
        return cls(vlm, text)

    async def recognize(self, image: bytes) -> PerceptionResult:
        """Send screenshot to VLM, parse the game state JSON."""
        raw = await self._vlm.chat(
            system_prompt=self._prompt,
            user_prompt="Output the game state as JSON.",
            image=image,
        )

        json_str = self._extract_json(raw)
        if not json_str:
            logger.warning("VLM response contained no valid JSON")
            return PerceptionResult(raw_response=raw, parsed={})

        state = json.loads(json_str)
        phase = state.get("phase", "unknown")
        buttons = len(state.get("buttons", []))
        cards = len(state.get("hand_cards", []))
        logger.info("Phase: %s | Buttons: %d | Cards: %d", phase, buttons, cards)

        return PerceptionResult(raw_response=raw, parsed=state)

    @staticmethod
    def _extract_json(text: str) -> str | None:
        text = text.strip()
        if text.startswith("```"):
            end = text.rfind("```")
            if end > 3:
                text = text[text.index("\n") + 1 : end].strip()
        m = JSON_RE.search(text)
        return m.group(0) if m else None

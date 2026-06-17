"""天天象棋感知 — VLM 棋盘识别。

每轮发送截图到 VLM，识别:
  - screen_type: 画面类型 (menu/playing/game_over)
  - board: 棋盘像素边界
  - pieces: 棋子列表 (双坐标: board_pos + pixel_pos)
  - buttons: UI 按钮
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from gameauto.core.perception.base import PerceptionResult
from gameauto.core.perception.vlm_client import VlmClient

logger = logging.getLogger("gameauto.xiangqi.perception")

JSON_RE = re.compile(r"\{[\s\S]*\}")


class XiangqiPerception:
    """基于 VLM 的天天象棋棋盘识别。"""

    def __init__(self, vlm: VlmClient, prompt_template: str) -> None:
        self._vlm = vlm
        self._prompt = prompt_template

    @classmethod
    def from_prompt_file(cls, vlm: VlmClient, prompt_path: str) -> "XiangqiPerception":
        text = Path(prompt_path).read_text(encoding="utf-8")
        return cls(vlm, text)

    async def recognize(self, image: bytes) -> PerceptionResult:
        """发送截图 → VLM → 解析棋盘 JSON。"""
        raw = await self._vlm.chat(
            system_prompt=self._prompt,
            user_prompt="Output the chess board state as JSON.",
            image=image,
        )

        json_str = self._extract_json(raw)
        if not json_str:
            logger.warning("VLM response contained no valid JSON")
            return PerceptionResult(raw_response=raw, parsed={})

        state = json.loads(json_str)
        state = self._validate(state)

        screen_type = state.get("screen_type", "unknown")
        n_pieces = len(state.get("pieces", []))
        n_buttons = len(state.get("buttons", []))
        logger.info("Screen: %s | Pieces: %d | Buttons: %d", screen_type, n_pieces, n_buttons)

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

    @staticmethod
    def _validate(state: dict) -> dict:
        """校验并修正 VLM 输出。"""
        pieces = state.get("pieces", [])
        if not pieces:
            return state

        valid = []
        for p in pieces:
            bp = p.get("board_pos", {})
            pp = p.get("pixel_pos", {})
            col = bp.get("col", 0)
            row = bp.get("row", 0)
            x = pp.get("x", 0)
            y = pp.get("y", 0)
            # 跳过无效棋子
            if not (1 <= col <= 9 and 1 <= row <= 10):
                continue
            if x <= 0 and y <= 0:
                continue
            valid.append(p)

        if len(valid) != len(pieces):
            logger.warning("Filtered %d invalid pieces", len(pieces) - len(valid))
            state["pieces"] = valid

        return state

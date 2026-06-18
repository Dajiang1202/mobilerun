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
        state = self._normalize(state)
        state = self._validate(state)

        screen_type = state.get("screen_type", "unknown")
        grid = state.get("grid", [])
        n_pieces = sum(1 for row in grid for c in row if c != "0") if grid else 0
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
    def _normalize(state: dict) -> dict:
        """标准化字段名（兼容新旧格式）。"""
        # screen → screen_type
        if "screen" in state and "screen_type" not in state:
            state["screen_type"] = state.pop("screen")
        # btns → buttons
        if "btns" in state and "buttons" not in state:
            state["buttons"] = state.pop("btns")
        # board compact: {"l":60,"t":120,"r":940,"b":880} → {"left":60,...}
        b = state.get("board", {})
        if b and "l" in b:
            state["board"] = {"left": b["l"], "top": b["t"], "right": b["r"], "bottom": b["b"]}
        return state

    @staticmethod
    def _validate(state: dict) -> dict:
        """校验棋盘网格格式。"""
        grid = state.get("grid", [])
        if not grid:
            return state

        # 确保10行每行9字符
        valid = []
        for row in grid[:10]:
            row_str = str(row)[:9].ljust(9, "0")
            valid.append(row_str)
        while len(valid) < 10:
            valid.append("0" * 9)
        state["grid"] = valid

        # 校验棋子数量（正常对局 2-32 子）
        n = sum(1 for r in valid for c in r if c != "0")
        if n < 2:
            logger.warning("Too few pieces: %d", n)
        elif n > 32:
            logger.warning("Too many pieces: %d, truncating", n)

        return state

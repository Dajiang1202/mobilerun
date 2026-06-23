"""斗地主感知 — VLM 屏幕识别。

每轮发送截图到 VLM，识别:
  - screen_type: 画面类型 (start 开局 / waiting 等待 / playing 出牌)
  - buttons: 所有按钮 (text + color + 坐标)
  - hand_cards: 手牌位置 (suit + value + x,y 坐标)

VLM 输出格式:
  {"screen_type": "playing", "buttons": [...], "hand_cards": [...]}

关键设计:
  - 基于按钮文字内容决策，不依赖 enabled 状态
  - VLM 返回按钮颜色辅助调试可视化
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from gameauto.core.perception.base import PerceptionResult
from gameauto.core.perception.vlm_client import VlmClient

logger = logging.getLogger("gameauto.doudizhu.perception")

# 从 VLM 文本中提取最外层 JSON
JSON_RE = re.compile(r"\{[\s\S]*\}")


class DouDiZhuPerception:
    """基于 VLM 的斗地主屏幕识别。

    使用 jinja2 提示词，告诉 VLM 输出结构化 JSON。
    不依赖任何 CV 方法，纯 VLM 方案。
    """

    def __init__(self, vlm: VlmClient, prompt_template: str) -> None:
        self._vlm = vlm
        self._prompt = prompt_template

    @classmethod
    def from_prompt_file(cls, vlm: VlmClient, prompt_path: str) -> "DouDiZhuPerception":
        text = Path(prompt_path).read_text(encoding="utf-8")
        return cls(vlm, text)

    async def recognize(self, image: bytes) -> PerceptionResult:
        """发送截图 → VLM → 解析 JSON。"""
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
        screen_type = state.get("screen_type", "unknown")
        buttons = len(state.get("buttons", []))
        cards = len(state.get("hand_cards", []))
        logger.info("Screen: %s | Buttons: %d | Cards: %d", screen_type, buttons, cards)

        return PerceptionResult(raw_response=raw, parsed=state)

    @staticmethod
    def _extract_json(text: str) -> str | None:
        """从 VLM 原始返回中提取 JSON。处理 markdown 代码块包裹。"""
        text = text.strip()
        if text.startswith("```"):
            end = text.rfind("```")
            if end > 3:
                text = text[text.index("\n") + 1 : end].strip()
        m = JSON_RE.search(text)
        return m.group(0) if m else None

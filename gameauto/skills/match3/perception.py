"""Match-3 感知 — VLM 棋盘识别。

流程:
  1. 组装 jinja2 提示词（告诉 VLM 输出棋盘 JSON）
  2. 调用 VlmClient.chat() 发送截图 + prompt
  3. 从响应中提取 JSON
  4. 验证/纠错棋盘数据（修正常见 VLM 错误）
"""

from __future__ import annotations

import json
import logging
import re

from gameauto.core.perception.base import PerceptionResult
from gameauto.core.perception.vlm_client import VlmClient

logger = logging.getLogger("gameauto.match3.perception")

# 从 VLM 文本中提取最外层 JSON 对象
JSON_RE = re.compile(r"\{[\s\S]*\}")


class Match3Perception:
    """基于 VLM 的消消乐棋盘识别。

    不直接调 VLM，而是通过 VlmClient（通用 OpenAI-compatible 接口）。
    这样切换模型（GPT / Qwen / Gemini）只需要改配置文件，不需要改代码。

    用法:
        vlm = VlmClient(model="qwen3-vl-flash", base_url="...", api_key="...")
        perception = Match3Perception.from_prompt_file(vlm, "prompts/generic.jinja2")
        result = await perception.recognize(screenshot_bytes)
        board = result.parsed  # {"rows": 7, "cols": 7, "tiles": [...], ...}
    """

    def __init__(self, vlm: VlmClient, prompt_template: str) -> None:
        """
        Args:
            vlm: VlmClient 实例。
            prompt_template: jinja2 模板字符串（system prompt）。
        """
        self._vlm = vlm
        self._prompt = prompt_template

    @classmethod
    def from_prompt_file(cls, vlm: VlmClient, prompt_path: str) -> "Match3Perception":
        """从 jinja2 文件创建，比直接传字符串更方便。"""
        from pathlib import Path
        text = Path(prompt_path).read_text(encoding="utf-8")
        return cls(vlm, text)

    async def recognize(self, image: bytes) -> PerceptionResult:
        """发送截图到 VLM，解析返回的棋盘 JSON。

        Returns:
            PerceptionResult，其中 .parsed 是 board dict。
            如果 VLM 没返回有效 JSON，.parsed 为空 dict。
        """
        # 调用 VLM（temperature=0.2，无 reasoning_effort，快速模式）
        raw = await self._vlm.chat(
            system_prompt=self._prompt,
            user_prompt="Output the board as JSON.",
            image=image,
        )

        # 提取 JSON（处理 markdown 代码块包裹的情况）
        json_str = self._extract_json(raw)
        if not json_str:
            logger.warning("VLM response contained no valid JSON")
            return PerceptionResult(raw_response=raw, parsed={})

        # 解析 + 纠错
        board = json.loads(json_str)
        board = self._expand_compact_board(board)
        board = self._validate_and_correct_board(board)

        rows = board.get("rows", "?")
        cols = board.get("cols", "?")
        logger.info("Board: %sx%s", rows, cols)

        return PerceptionResult(raw_response=raw, parsed=board)

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_json(text: str) -> str | None:
        """从 VLM 响应中提取最外层 JSON 对象。

        处理常见格式:
          - 裸 JSON: {"rows": 7, ...}
          - Markdown 代码块: ```json\\n{...}\\n```
        """
        text = text.strip()
        if text.startswith("```"):
            end = text.rfind("```")
            if end > 3:
                text = text[text.index("\n") + 1 : end].strip()
        m = JSON_RE.search(text)
        return m.group(0) if m else None

    @staticmethod
    def _expand_compact_board(board: dict) -> dict:
        """将紧凑格式（legend + 行字符串）展开为完整 tiles 网格。

        紧凑格式（省 token）:
            {"legend": {"B": "blue_bear", ...}, "tiles": ["BGNBGNB", ...]}
        特殊码: "." => empty, "#" => blocked。
        若 tiles 已经是 list[list]（旧格式或已展开），原样返回（向后兼容）。

        展开后下游 solver/visualizer/board.json 完全无感。
        """
        tiles = board.get("tiles")
        if not isinstance(tiles, list) or not tiles:
            return board
        # 已经是二维数组（旧格式）→ 不处理
        if all(isinstance(row, list) for row in tiles):
            return board

        legend = board.get("legend", {}) or {}
        legend = {str(k): str(v) for k, v in legend.items()}

        expanded: list[list[str]] = []
        for row in tiles:
            if not isinstance(row, str):
                expanded.append([str(row)])
                continue
            row = row.rstrip()
            cells: list[str] = []
            for ch in row:
                if ch == ".":
                    cells.append("empty")
                elif ch == "#":
                    cells.append("blocked")
                elif ch in legend:
                    cells.append(legend[ch])
                else:
                    # 未知码：原样保留，solver 会把它当作独立类型处理
                    cells.append(ch)
            expanded.append(cells)

        board["tiles"] = expanded
        # legend 已展开完毕，丢弃以保持 board.json 干净
        board.pop("legend", None)
        return board

    @staticmethod
    def _validate_and_correct_board(board: dict) -> dict:
        """修正 VLM 常见错误。

        常见错误:
          1. 列数多算（9 列识别成 10 列）→ 检查末列是否可疑
          2. 行长度不一致 → 统一补齐/截断
        """
        tiles = board.get("tiles", [])
        if not tiles:
            return board

        rows = len(tiles)
        cols = board.get("cols", len(tiles[0]) if tiles else 0)

        # 修复 1: 10 列经常误识别为 9 列
        if cols == 10 and rows >= 7:
            suspicious = False
            for row in tiles:
                if len(row) >= 10 and row[-1] == row[-2]:
                    suspicious = True
                    break
            board_width = board.get("board_right", 1000) - board.get("board_left", 0)
            if rows > 0:
                board_height = board.get("board_bottom", 1000) - board.get("board_top", 0)
                if board_width / 10 / (board_height / rows) > 1.3:
                    suspicious = True
            if suspicious:
                cols = 9
                tiles = [row[:9] if len(row) >= 9 else row for row in tiles]
                board["rows"] = rows
                board["cols"] = 9
                board["tiles"] = tiles
                logger.info("Corrected board to %sx%s (removed extra column)", rows, cols)

        # 修复 2: 所有行补齐到相同列数
        target_cols = board.get("cols", len(tiles[0]) if tiles else 0)
        normalized = []
        for row in tiles:
            if len(row) > target_cols:
                normalized.append(row[:target_cols])
            elif len(row) < target_cols:
                normalized.append(row + ["empty"] * (target_cols - len(row)))
            else:
                normalized.append(row)
        board["tiles"] = normalized
        board["rows"] = len(normalized)
        board["cols"] = target_cols

        return board

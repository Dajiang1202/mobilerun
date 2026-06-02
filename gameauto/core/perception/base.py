"""感知层基础类型 — 所有视觉识别任务的统一接口。

感知 = 把截图变成结构化数据。可以是:
  - VLM: 大模型看图输出 JSON
  - OCR: 文字区域检测 + 识别
  - 模板匹配: OpenCV matchTemplate
  - YOLO: 目标检测
  - 自定义: 任意 Python 函数

PerceptionPipeline 负责并行调度多个感知子任务，
总延迟 = max(子任务延迟)，不是累加。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field


class PerceptionTask(BaseModel):
    """单个感知子任务定义 — 告诉 Pipeline 要做什么。

    示例（消消乐 VLM）:
        PerceptionTask(name="board", task_type="vlm", prompt=board_prompt)

    示例（金铲铲 OCR）:
        PerceptionTask(name="gold", task_type="ocr", roi=(0.02, 0.88, 0.08, 0.93))
    """
    name: str                                          # 任务名（用于日志和结果索引）
    task_type: Literal["vlm", "ocr", "template_match", "yolo", "color_detect", "custom"] = "vlm"
    prompt: str | None = None                          # VLM 专用: system prompt
    roi: tuple[float, float, float, float] | None = None  # CV 专用: (left, top, right, bottom) 归一化 [0-1]
    config: dict = Field(default_factory=dict)          # 额外参数（阈值、模型名等）


class PerceptionResult(BaseModel):
    """单次感知的完整输出。

    raw_response: VLM 原始文本
    parsed: 解析后的结构化数据（如棋盘 JSON）
    tasks_output: 多任务并行时每个子任务的输出 {task_name: result}
    latency_ms: 感知总耗时（毫秒）
    """
    raw_response: str | None = None
    parsed: dict = Field(default_factory=dict)
    tasks_output: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0


class BasePerception(ABC):
    """感知抽象 — 每个 Skill 实现自己的感知逻辑。

    消消乐: VLM 识别棋盘 → 解析为 JSON
    金铲铲: OCR 金币 + 模板匹配棋子 + YOLO 敌人 → 合并为 GameState
    """

    @abstractmethod
    async def recognize(self, image: bytes) -> PerceptionResult:
        """处理一张截图，返回结构化感知数据。

        实现可以是单任务（消消乐只有一个 VLM 调用），
        也可以是多任务并行（金铲铲 5 个子任务同时跑）。
        """
        ...

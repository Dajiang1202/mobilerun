"""DataRecorder — 逐帧录制截图、感知结果、决策、操作。

纯视觉方案的核心保障: 游戏不能暂停，所以必须录制每帧数据，
用于离线回灌调试和策略分析。

录制结构:
    logs/<session_id>/frames/
    ├── 000000/
    │   ├── screenshot.png      ← 原始截图
    │   ├── perception.json     ← 感知输出（VLM 原始文本 + 解析结果）
    │   ├── decisions.json      ← 决策输出（交换列表）
    │   └── actions.json        ← 操作序列
    └── ...
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from gameauto.core.orchestration.base import Action
from gameauto.core.recorder.session import SessionManager

logger = logging.getLogger("gameauto.recorder")


class DataRecorder:
    """逐帧数据录制器。

    零侵入: 不影响主循环性能，所有 I/O 是同步写入（文件小，开销可忽略）。
    """

    def __init__(self, session: SessionManager) -> None:
        self._session = session

    def record_frame(
        self,
        frame_id: int,
        screenshot: bytes,
        perception_raw: str | None = None,
        perception_parsed: dict | None = None,
        decisions: list[dict] | None = None,
        actions: list[Action] | None = None,
    ) -> Path:
        """录制一帧的完整数据。

        Args:
            frame_id: 帧序号（从 0 开始）
            screenshot: 原始截图字节流
            perception_raw: VLM 原始返回文本
            perception_parsed: 解析后的结构化数据（如棋盘 JSON）
            decisions: 决策引擎输出
            actions: 操作序列（Action 对象列表，自动序列化）
        """
        frame_dir = self._session.frame_dir(frame_id)

        (frame_dir / "screenshot.png").write_bytes(screenshot)

        perception_data = {
            "raw_response": perception_raw,
            "parsed": perception_parsed or {},
        }
        (frame_dir / "perception.json").write_text(
            json.dumps(perception_data, ensure_ascii=False, indent=2), encoding="utf-8",
        )

        if decisions:
            (frame_dir / "decisions.json").write_text(
                json.dumps(decisions, ensure_ascii=False, indent=2), encoding="utf-8",
            )

        if actions:
            actions_data = [a.model_dump() for a in actions]
            (frame_dir / "actions.json").write_text(
                json.dumps(actions_data, ensure_ascii=False, indent=2), encoding="utf-8",
            )

        return frame_dir

    def record_event(self, frame_id: int, event_type: str, data: dict[str, Any]) -> None:
        """录制离散事件（状态切换、错误、LLM 调用等）。"""
        path = self._session.event_path(frame_id, event_type)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def record_summary(self, summary: dict[str, Any]) -> None:
        """会话结束时写入总结。"""
        self._session.write_summary(summary)

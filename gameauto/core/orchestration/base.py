"""任务调度基础类型 — GameState 和 Action。

GameState 枚举定义了框架内置的游戏状态。每个 Skill 可以按需注册。
新增游戏时，直接复用现有状态或扩展新的枚举值。

Action 是框架和执行层之间的唯一接口：
感知和决策产出 Action[]，GameLoop 逐个执行。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


# Game states are plain strings so every skill can define its own
# without modifying framework code. Use the constants below as conventions.
class _GS:
    """GameState constants — use these or define your own strings."""
    UNKNOWN = "unknown"
    DESKTOP = "desktop"
    GAME_MENU = "game_menu"
    IN_GAME = "in_game"
    SETTLEMENT = "settlement"
    PAUSED = "paused"
    # Card games
    BIDDING = "bidding"      # 叫地主/抢地主
    PLAYING = "playing"      # 出牌阶段


GameState = _GS  # alias for convenience


class Action(BaseModel):
    """一次设备操作，由 Skill 的决策逻辑产出，GameLoop 负责执行。

    坐标使用归一化 [0-1000]，运行时由 Input 层转换为设备像素。

    示例（消消乐 swipe）:
        Action(type="swipe", x1=500, y1=600, x2=500, y2=700, duration_ms=1000)

    示例（金铲铲 tap）:
        Action(type="tap", x1=800, y1=900, description="点击开始匹配")
    """
    type: Literal["swipe", "tap", "wait", "drag"]   # 操作类型
    x1: float | None = None                          # 起点 x（归一化 [0-1000]）
    y1: float | None = None                          # 起点 y
    x2: float | None = None                          # 终点 x（swipe/drag 专用）
    y2: float | None = None                          # 终点 y
    duration_ms: int = 1000                          # 持续时间（swipe/tap/wait）
    description: str = ""                            # 人类可读描述（用于日志和调试）

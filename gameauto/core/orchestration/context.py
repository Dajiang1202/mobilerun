"""运行时游戏上下文 — 贯穿整个会话的共享状态。

GameContext 在 run_match3.py 中初始化，然后传入 GameLoop 和各个 State handler。
它承载会话级别的信息：当前状态、回合计数、分辨率、会话目录等。

线程安全: 当前设计是单线程异步，不需要锁。如果未来引入多线程，
需要给可变字段加 asyncio.Lock。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from gameauto.core.orchestration.base import GameState


@dataclass
class GameContext:
    """会话运行时上下文。

    所有可变状态集中在这里，handler 通过 context 读取/修改。
    """
    state: GameState = GameState.UNKNOWN         # 当前游戏状态
    round_num: int = 0                            # 当前轮次（从 1 开始）
    session_dir: Path = field(default_factory=lambda: Path("logs"))  # 会话日志目录
    capture_resolution: tuple[int, int] = (0, 0)  # 截图原始分辨率
    input_resolution: tuple[int, int] = (0, 0)    # 输入坐标空间分辨率
    max_rounds: int = 10                           # 总轮数
    max_steps_per_round: int = 1                   # 每轮最多操作步数
    success_count: int = 0                         # 成功执行的操作总数

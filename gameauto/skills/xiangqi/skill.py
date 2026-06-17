"""XiangqiSkill — 天天象棋 Skill 门面。

单个游戏状态(PLAYING)，由 VLM 感知 screen_type 后路由:
  - menu: 点击开始游戏
  - playing: VLM 识别棋盘 → 引擎搜索 → 点击走子
  - game_over: 点击再来一局
"""

from __future__ import annotations

from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.skills.xiangqi.perception import XiangqiPerception
from gameauto.skills.xiangqi.states import XiangqiStateRegistrar


class XiangqiSkill:
    """天天象棋 Skill 插件。"""

    def __init__(self, perception: XiangqiPerception) -> None:
        self.perception = perception
        self._registrar = XiangqiStateRegistrar(perception)

    def register_states(self, sm: StateMachine) -> None:
        self._registrar.register(sm)

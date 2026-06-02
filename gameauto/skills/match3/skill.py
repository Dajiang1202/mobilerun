"""Match3Skill — 消消乐 Skill 的门面类。

这是 ISkill 接口的消消乐实现。内部委托给:
  - Match3Perception: VLM 识别棋盘
  - solve_board_multi:  贪心求解器
  - Match3StateRegistrar: 状态注册

新增游戏时参考这个文件的结构，实现你自己的 Skill。
"""

from __future__ import annotations

from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.skills.match3.perception import Match3Perception
from gameauto.skills.match3.states import Match3StateRegistrar


class Match3Skill:
    """消消乐 Skill 插件。

    用法:
        skill = Match3Skill(perception, max_steps=2)
        skill.register_states(state_machine)
    """

    def __init__(self, perception: Match3Perception, max_steps: int = 1) -> None:
        self.perception = perception
        self._registrar = Match3StateRegistrar(perception, max_steps=max_steps)

    def register_states(self, sm: StateMachine) -> None:
        """向状态机注册消消乐的游戏状态（M1: 仅 IN_GAME）。"""
        self._registrar.register(sm)

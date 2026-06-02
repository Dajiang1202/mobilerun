"""DouDiZhuSkill — 斗地主 Skill 门面。

两个游戏状态:
  - BIDDING: 叫牌阶段（叫地主/抢地主/不叫/不加倍）
  - PLAYING: 出牌阶段（出牌/不出/提示）

使用方式与 Match3Skill 完全一致:
  skill = DouDiZhuSkill(perception)
  skill.register_states(state_machine)
"""

from __future__ import annotations

from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.skills.doudizhu.perception import DouDiZhuPerception
from gameauto.skills.doudizhu.states import DouDiZhuStateRegistrar


class DouDiZhuSkill:
    """斗地主 Skill 插件 — 实现 ISkill 接口。

    封装了 perception → decision → action 的完整流程。
    GameLoop 不需要知道内部细节，只通过 StateMachine 调用。
    """

    def __init__(self, perception: DouDiZhuPerception) -> None:
        self.perception = perception
        self._registrar = DouDiZhuStateRegistrar(perception)

    def register_states(self, sm: StateMachine) -> None:
        """向全局状态机注册斗地主的所有游戏状态。"""
        self._registrar.register(sm)

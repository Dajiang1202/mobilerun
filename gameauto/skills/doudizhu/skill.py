"""DouDiZhuSkill — 斗地主 Skill 门面类。

两个状态:
  - BIDDING (叫地主/抢地主/不叫/不加倍): 随机点击按钮
  - PLAYING (出牌/不出/提示): 点击"提示" → 点击"出牌"
"""

from __future__ import annotations

from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.skills.doudizhu.perception import DouDiZhuPerception
from gameauto.skills.doudizhu.states import DouDiZhuStateRegistrar


class DouDiZhuSkill:
    """斗地主 Skill 插件。

    用法:
        skill = DouDiZhuSkill(perception)
        skill.register_states(state_machine)
    """

    def __init__(self, perception: DouDiZhuPerception) -> None:
        self.perception = perception
        self._registrar = DouDiZhuStateRegistrar(perception)

    def register_states(self, sm: StateMachine) -> None:
        """注册 BIDDING 和 PLAYING 两个状态。"""
        self._registrar.register(sm)

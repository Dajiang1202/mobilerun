"""TftSkill — 金铲铲 Skill 的门面类。

委托给:
  - TftPerception: OCR + 模板匹配 Pipeline
  - TftDecision: L1 固定策略决策
  - TftStateRegistrar: 4 状态注册

参照 match3/skill.py 的模式。
"""

from __future__ import annotations

from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.core.perception.cv.template_match import TemplateMatchTask
from gameauto.skills.tft.decision import TftDecision
from gameauto.skills.tft.perception import TftPerception
from gameauto.skills.tft.states import TftStateRegistrar


class TftSkill:
    """金铲铲 Skill 插件。

    用法:
        skill = TftSkill(perception, decision, tm_task)
        skill.register_states(state_machine)
    """

    def __init__(
        self,
        perception: TftPerception,
        decision: TftDecision,
        tm_task: TemplateMatchTask,
    ) -> None:
        self.perception = perception
        self.decision = decision
        self._registrar = TftStateRegistrar(perception, decision, tm_task)

    def register_states(self, sm: StateMachine) -> None:
        """向状态机注册金铲铲的 4 个游戏状态。"""
        self._registrar.register(sm)

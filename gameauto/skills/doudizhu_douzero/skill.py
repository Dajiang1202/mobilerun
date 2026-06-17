"""DouDiZhuDouzeroSkill — DouZero-powered DouDiZhu Skill facade.

Usage:
    perception = DouDiZhuDouzeroPerception(template_dir=...)
    decision = DouzeroDecision(model_dir=...)
    skill = DouDiZhuDouzeroSkill(perception, decision)
    skill.register_states(state_machine)
"""

from __future__ import annotations

from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.skills.doudizhu_douzero.perception import DouDiZhuDouzeroPerception
from gameauto.skills.doudizhu_douzero.decision import DouzeroDecision
from gameauto.skills.doudizhu_douzero.states import DouDiZhuDouzeroStateRegistrar


class DouDiZhuDouzeroSkill:
    """DouZero-powered DouDiZhu Skill plugin.

    Encapsulates CV perception → DeepAgent decision → HDC action pipeline.
    """

    def __init__(
        self,
        perception: DouDiZhuDouzeroPerception,
        decision: DouzeroDecision,
    ) -> None:
        self.perception = perception
        self.decision = decision
        self._registrar = DouDiZhuDouzeroStateRegistrar(perception, decision)

    def register_states(self, sm: StateMachine) -> None:
        """Register DouDiZhu DouZero states with the state machine."""
        self._registrar.register(sm)

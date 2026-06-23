"""DouDiZhuSkill — 斗地主 Skill 门面。

统一游戏状态，由 VLM 感知 screen_type 后路由决策:
  - start: 开局/结算界面 → 点击开始游戏
  - waiting: 等待他人出牌 → 不做操作
  - playing: 有牌有按钮 → 按优先级: 不叫→不加倍→提示+出牌→要不起

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

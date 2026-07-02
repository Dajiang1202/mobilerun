"""TFT 顶层工作流 —— 组合中层动作, 产出整轮 list[Action]。

输入: perception 给的位置 (棋子点击点 / 装备掉落点) + TftActions 构造器。
输出: list[Action] (归一化 [0-1000])。
  - 回放: 画出来 (tap=圆点, drag=箭头)
  - 真机: BaseInput 逐个执行 (GameLoop 负责步间等待动画)

注: 这里产出的是"计划"——一次性把要点的位置都列出来。
真机执行时的点击→等面板→OCR→关闭→下一个 的时序, 由决策循环在外层加 wait Action。
"""

from __future__ import annotations

from gameauto.core.orchestration.base import Action
from gameauto.skills.tft.actions import TftActions


def iterate_champions(builder: TftActions,
                      board_clicks: list[tuple[int, int]],
                      bench_clicks: list[tuple[int, int]] | None = None) -> list[Action]:
    """遍历所有我方棋子 (棋盘 + 战备), 逐个点击弹面板。

    Args:
        builder: TftActions (持有 rois + 分辨率)。
        board_clicks: 棋盘棋子点击点 (像素), 来自血条检测。
        bench_clicks: 战备区棋子点击点 (像素), 可选。
    """
    actions: list[Action] = []
    for i, pos in enumerate(board_clicks):
        actions += builder.click_champion(pos)
        # 给最后一条加序号描述, 方便 viz/日志辨认
        if actions:
            actions[-1] = actions[-1].model_copy(update={"description": f"点击棋盘棋子{i}"})
    for i, pos in enumerate(bench_clicks or []):
        actions += builder.click_champion(pos)
        if actions:
            actions[-1] = actions[-1].model_copy(update={"description": f"点击战备棋子{i}"})
    return actions


def iterate_item_drops(builder: TftActions,
                       drop_positions: list[tuple[int, int]]) -> list[Action]:
    """遍历棋盘上的圆形装备掉落物, 逐个点击拾取/查看。

    Args:
        drop_positions: 掉落物位置 (像素), 来自圆形检测 (待实现)。
    """
    actions: list[Action] = []
    for i, pos in enumerate(drop_positions):
        actions += builder.click_champion(pos)  # 点击动作通用, 复用
        if actions:
            actions[-1] = actions[-1].model_copy(update={"description": f"拾取掉落物{i}"})
    return actions


def buy_all_core(builder: TftActions, shop_texts: list[str], core_names: list[str]) -> list[Action]:
    """示例短期规则: 商店里出现核心棋子就买。

    Args:
        shop_texts: 5 个商店槽的 OCR 文本。
        core_names: 要买的核心棋子名 (模糊匹配)。
    """
    actions: list[Action] = []
    for idx, txt in enumerate(shop_texts):
        if any(core in txt for core in core_names):
            actions += builder.buy_shop_slot(idx)
    return actions

"""斗地主决策引擎 — 基于按钮文字内容的规则驱动。

策略（按优先级）:
  1. 开局/结算界面: 点击"开始游戏"或"继续游戏"
  2. 等待界面 (无按钮): 不做操作
  3. 有牌有按钮:
     - "不叫" → 点击不叫（永远不叫地主）
     - "不加倍" → 点击不加倍（永远不加倍）
     - "提示" + "出牌" → 先点提示，再点出牌
     - "要不起" → 点击要不起
  4. 终止条件: round > 20
"""

from __future__ import annotations

import logging

from gameauto.core.orchestration.base import Action

logger = logging.getLogger("gameauto.doudizhu.decision")

# 开局/结算界面按钮 — 点击后进入下一局
_START_BUTTONS = {"开始游戏", "继续游戏"}

# 追踪"继续游戏"出现次数（跨回合），第2次遇到则终止
_continue_game_seen = 0

# 每轮操作后等待5秒再进入下一轮识别
_WAIT_5S = Action(type="wait", duration_ms=5000, description="Wait 5s before next round")
# 开局/继续游戏后等待10秒（加载/匹配时间更长）
_WAIT_10S = Action(type="wait", duration_ms=10000, description="Wait 10s after start/continue")


def decide(state: dict, round_num: int) -> list[Action]:
    """统一决策入口。

    Args:
        state: VLM 返回的解析后状态 dict，包含 screen_type, buttons, hand_cards
        round_num: 当前回合数

    Returns:
        要执行的 Action 列表，空列表表示本轮无操作或游戏终止。
    """
    screen_type = state.get("screen_type", "unknown")
    buttons = state.get("buttons", [])
    button_texts = {b.get("text", "") for b in buttons}

    logger.info(
        "Decision: screen_type=%s, round=%d, buttons=%s",
        screen_type, round_num, button_texts,
    )

    # ── 开局/结算界面 ──────────────────────────────────────────
    if button_texts & _START_BUTTONS:
        target = "开始游戏" if "开始游戏" in button_texts else "继续游戏"
        btn = _find_button(buttons, target)
        logger.info("Start/Continue: clicking '%s' at (%d,%d)", btn["text"], btn["x"], btn["y"])
        return [_make_tap(btn, f"Click '{target}'"), _WAIT_10S]

    if not buttons:
        logger.info("No buttons — waiting or idle, no action")
        return []

    # ── 有牌有按钮: 按优先级处理 ──────────────────────────────

    # 优先级 1: 不叫
    if "不叫" in button_texts:
        btn = _find_button(buttons, "不叫")
        logger.info("Bidding: clicking '%s' at (%d,%d)", btn["text"], btn["x"], btn["y"])
        return [_make_tap(btn, "Click '不叫'"), _WAIT_5S]

    # 优先级 2: 不加倍
    if "不加倍" in button_texts:
        btn = _find_button(buttons, "不加倍")
        logger.info("Double: clicking '%s' at (%d,%d)", btn["text"], btn["x"], btn["y"])
        return [_make_tap(btn, "Click '不加倍'"), _WAIT_5S]

    # 优先级 3: 要不起
    if "要不起" in button_texts:
        btn = _find_button(buttons, "要不起")
        logger.info("Pass: clicking '%s' at (%d,%d)", btn["text"], btn["x"], btn["y"])
        return [_make_tap(btn, "Click '要不起'"), _WAIT_5S]

    # 优先级 4: 提示 + 出牌
    if "提示" in button_texts and "出牌" in button_texts:
        hint = _find_button(buttons, "提示")
        play = _find_button(buttons, "出牌")
        logger.info("Playing: hint at (%d,%d) → play at (%d,%d)",
                     hint["x"], hint["y"], play["x"], play["y"])
        return [
            _make_tap(hint, "Click '提示'"),
            _make_tap(play, "Click '出牌'"),
            _WAIT_5S,
        ]

    # 兜底: 不认识按钮组合，记录警告
    logger.warning("Unknown button combination: %s", button_texts)
    return []


def is_game_over(state: dict, round_num: int) -> bool:
    """检查是否满足游戏结束条件。

    终止条件:
      - round > 20
      - 第2次遇到"继续游戏"按钮（一局结束后出现结算界面）
    """
    global _continue_game_seen

    if round_num > 20:
        return True

    buttons = state.get("buttons", [])
    button_texts = {b.get("text", "") for b in buttons}
    if "继续游戏" in button_texts:
        _continue_game_seen += 1
        logger.info("'继续游戏' seen %d time(s)", _continue_game_seen)
        if _continue_game_seen >= 2:
            logger.info("Game over: 2nd '继续游戏' detected")
            return True
        return False

    return False


def reset_continue_count() -> None:
    """重置继续游戏计数（用于测试或重新开始）。"""
    global _continue_game_seen
    _continue_game_seen = 0


# ── helpers ──────────────────────────────────────────────────────────

def _find_button(buttons: list[dict], text: str) -> dict:
    """按文字查找按钮，找不到则返回第一个按钮作为兜底。"""
    for b in buttons:
        if b.get("text") == text:
            return b
    logger.warning("Button '%s' not found in %s, using first button as fallback",
                   text, [b.get("text") for b in buttons])
    return buttons[0]


def _make_tap(btn: dict, description: str) -> Action:
    """从按钮 dict 构造 tap Action。"""
    return Action(
        type="tap",
        x1=btn["x"],
        y1=btn["y"],
        duration_ms=150,
        description=description,
    )

"""DouDiZhu DouZero CV perception — 分区模板匹配, 无 VLM 依赖。

分区匹配解决红黑互串 + 提速(<1s):
  - cards(手牌)只在手牌区, others(对手出牌/底牌)只在对手/底牌区 → 物理隔离
  - 底牌相对对手出牌区缩小约 65%, 用 scale 0.65 单独匹配
  - 「继续」(结算页)/「地主标」位置不固定, 全图搜
  - TM_CCOEFF_NORMED 减均值会削弱颜色(红3/黑3字形相同致互串), 命中后做颜色校验

像素 ROI 基于本机截图, 运行时按图尺寸转归一化, 详见 PX。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from gameauto.core.perception.base import PerceptionResult
from gameauto.core.perception.cv.template_match import TemplateMatchTask

logger = logging.getLogger("gameauto.doudizhu_douzero")

# 有效点数字符, 与 douzero/env/game.py 的 RealCard2EnvCard 一致
_RANKS = set("3456789TJQKA2XD")


def _strip_color(template: str) -> str | None:
    """卡牌模板名颜色前缀剥离: mb3/mrA/ob3/orD -> 点数; 非卡牌模板返回 None。"""
    if (len(template) == 3 and template[0] in "mo"
            and template[1] in "br" and template[2] in _RANKS):
        return template[2]
    return None


def _is_red(region_bgr: np.ndarray) -> bool:
    """区域是否偏红(红牌角标): 较多 R 明显高于 G/B 的像素。"""
    if region_bgr is None or region_bgr.size == 0:
        return False
    b = region_bgr[:, :, 0].astype(int)
    g = region_bgr[:, :, 1].astype(int)
    r = region_bgr[:, :, 2].astype(int)
    return ((r - g > 25) & (r - b > 25)).mean() > 0.05


def _color_matches(template: str, region_bgr: np.ndarray) -> bool:
    """模板颜色前缀(b黑/r红)与命中区域颜色是否一致; 仅校验 m/o 的 b/r 模板。"""
    if len(template) >= 2 and template[0] in "mo" and template[1] in "br":
        return _is_red(region_bgr) == (template[1] == "r")
    return True


class DouDiZhuDouzeroPerception:
    """分区模板匹配的斗地主视觉感知。

    产出 decision 需要的字段: phase / my_hand / card_positions / last_play /
    landlord_cards / is_pass / is_landlord / buttons。
    """

    # 像素 ROI: (x1, y1, x2, y2), 基于本机截图分辨率
    PX = {
        "hand":      (350, 770, 2600, 970),
        "landlord3": (1000, 0, 2100, 130),
        "play_up":   (700, 200, 1424, 600),
        "play_down": (1425, 200, 2100, 600),
        "buttons":   (500, 600, 2200, 800),
        "continue":  (1899, 957, 2848, 1276),   # 继续(右1/3屏, 下1/4屏)
        "ingame":    (2450, 1175, 2680, 1276),  # 游戏中独有小按钮
    }

    # (key, 子目录, ROI键|None, scales, 阈值, filter|None, role)
    # role 决定命中归入哪个字段
    TASKS = [
        ("hand",      "cards",   "hand",      [1.0],  0.88, None, "hand"),
        ("play_up",   "others",  "play_up",   [1.0],  0.85, None, "others_play_up"),
        ("play_down", "others",  "play_down", [1.0],  0.85, None, "others_play_down"),
        ("底牌",      "others",  "landlord3", [0.65], 0.80, None, "others_landlord"),
        ("buttons",   "buttons", "buttons",   [1.0],  0.88,
         ["叫地主", "不叫", "抢地主", "加倍", "不加倍", "出牌", "不出", "要不起"], "buttons"),
        ("继续",      "buttons", "continue",  [1.0],  0.85, ["继续"], "buttons"),
        ("开始游戏",  "buttons", "buttons",   [1.0],  0.85, ["开始游戏"], "buttons"),
        ("地主标",    "ui",      None,        [1.0],  0.72, ["landlord_words"], "landlord"),
        ("游戏中",    "ui",      "ingame",    [1.0],  0.85, ["ingame_marker"], "ingame"),
        ("不出上",    "ui",      "play_up",   [1.0],  0.85, ["pass"], "pass"),
        ("不出下",    "ui",      "play_down", [1.0],  0.85, ["pass"], "pass"),
    ]

    def __init__(
        self,
        template_dir: str,
        card_confidence: float = 0.88,
        button_confidence: float = 0.88,
        pass_confidence: float = 0.85,
        capture_scale: int = 1,
    ) -> None:
        self._template_dir = Path(template_dir)
        self._capture_scale = capture_scale  # scrcpy scale>1 时截图缩小: 模板scales÷scale, ROI用native
        self._matchers: dict[str, TemplateMatchTask] = {}
        # landlord 标志 + 底牌一局固定: 首次锁定后跳过(底牌需 3 张才锁, 否则下帧重试)
        self._landlord_locked = False
        self._landlord_cards_locked = False
        self._cached_is_landlord = False
        self._cached_landlord_cards: list[str] = []
        for sub in ("cards", "others", "buttons", "ui"):
            d = self._template_dir / sub
            if d.is_dir():
                self._matchers[sub] = TemplateMatchTask(str(d))
        logger.info("CV perception initialized: %d template sets loaded from %s",
                    len(self._matchers), self._template_dir)

    async def recognize(self, image: bytes, buttons_only: bool = False) -> PerceptionResult:
        """分区并行匹配 → 颜色校验 → 结构化 perception dict。

        buttons_only=True 时只跑按钮 + ui 标志(landlord/pass), 跳过手牌/对手/底牌
        (最耗时的 28+28 模板), 供「先看按钮、按需再识别手牌」的分层策略使用。
        """
        t0 = time.perf_counter()
        img = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            logger.warning("perception: failed to decode image")
            return PerceptionResult(raw_response="", parsed={}, tasks_output={}, latency_ms=0)
        h, w = img.shape[:2]
        # PX 按 native(原尺寸)标定; 截图若缩放(scrcpy scale>1), 用 native 归一化 ROI
        nw, nh = w * self._capture_scale, h * self._capture_scale

        def norm(rk):
            if rk is None:
                return None
            x1, y1, x2, y2 = self.PX[rk]
            return (x1 / nw, y1 / nh, x2 / nw, y2 / nh)

        active = [t for t in self.TASKS if t[1] in self._matchers]
        # 一局缓存: landlord/底牌锁定后跳过对应 task
        if self._landlord_locked:
            active = [t for t in active if t[6] != "landlord"]
        if self._landlord_cards_locked:
            active = [t for t in active if t[6] != "others_landlord"]
        if buttons_only:
            # 分层: 仅按钮 + ui 标志(含 ingame), 跳过手牌/对手/底牌
            active = [t for t in active if t[6] in ("buttons", "landlord", "pass", "ingame")]
        results = await asyncio.gather(*[
            self._matchers[sub].run(
                image, roi=norm(rk),
                config={"threshold": thr,
                        "scales": [s / self._capture_scale for s in scales], "nms_iou": 0.3,
                        **({"filter_names": fn} if fn else {})})
            for _key, sub, rk, scales, thr, fn, _role in active
        ])

        hand_m: list[dict] = []
        play_up_m: list[dict] = []
        play_down_m: list[dict] = []
        landlord3_m: list[dict] = []
        buttons: list[dict] = []
        is_pass = False
        is_landlord = False
        is_ingame = False
        for (_key, _sub, _rk, _sc, _thr, _fn, role), res in zip(active, results):
            for m in res.get("matches", []):
                region = img[m["y"]:m["y"] + m["h"], m["x"]:m["x"] + m["w"]]
                if not _color_matches(m["template"], region):
                    continue
                if role == "hand":
                    hand_m.append(m)
                elif role == "others_play_up":
                    play_up_m.append(m)
                elif role == "others_play_down":
                    play_down_m.append(m)
                elif role == "others_landlord":
                    landlord3_m.append(m)
                elif role == "landlord":
                    is_landlord = True
                elif role == "ingame":
                    is_ingame = True
                elif role == "pass":
                    is_pass = True
                elif role == "buttons":
                    buttons.append({
                        "text": m["template"],
                        "x": m["x"] + m["w"] // 2,
                        "y": m["y"] + m["h"] // 2,
                        "box": (m["x"], m["y"], m["w"], m["h"]),
                    })

        my_hand, card_positions = self._parse_card_matches(hand_m)
        last_play_up, _ = self._parse_card_matches(play_up_m)
        last_play_down, _ = self._parse_card_matches(play_down_m)
        landlord_cards, _ = self._parse_card_matches(landlord3_m)
        # 一局缓存: landlord 标志首次命中即锁; 底牌需 3 张才锁(否则下帧重试)
        if self._landlord_locked:
            is_landlord = self._cached_is_landlord
        elif is_landlord:
            self._landlord_locked = True
            self._cached_is_landlord = True
        if self._landlord_cards_locked:
            landlord_cards = self._cached_landlord_cards
        elif len(landlord_cards) == 3:
            self._landlord_cards_locked = True
            self._cached_landlord_cards = landlord_cards
        # 要压的牌: 上家(play_up, 我前一位)非 pass 则压上家; 上家 pass 则压下家
        # (绝不能把两区合并 —— 那会把两家各出的牌拼成非法牌型)
        last_play = last_play_up if last_play_up else last_play_down

        button_names = [b["text"] for b in buttons]
        phase = "playing"
        if any("开始游戏" in n for n in button_names):
            phase = "lobby"
        elif any(k in n for n in button_names for k in ["叫地主", "抢地主", "加倍", "不加倍"]):
            phase = "bidding"
        elif any("继续" in n for n in button_names):
            phase = "settlement"

        # 坐标归一化到 [0-1000](框架 Action 约定), decision/执行层直接用
        card_pos_norm = {
            k: [[round(px / w * 1000, 1), round(py / h * 1000, 1)] for (px, py) in v]
            for k, v in card_positions.items()
        }
        buttons_norm = [
            {"text": b["text"], "x": round(b["x"] / w * 1000, 1), "y": round(b["y"] / h * 1000, 1)}
            for b in buttons
        ]

        parsed = {
            "phase": phase,
            "my_hand": my_hand,
            "last_play": last_play,
            "last_play_up": last_play_up,
            "last_play_down": last_play_down,
            "landlord_cards": landlord_cards,
            "is_pass": is_pass,
            "is_landlord": is_landlord,
            "is_ingame": is_ingame,
            "buttons": buttons_norm,
            "button_names": button_names,
            "card_positions": card_pos_norm,
        }
        latency = (time.perf_counter() - t0) * 1000
        return PerceptionResult(raw_response="", parsed=parsed,
                                tasks_output={}, latency_ms=round(latency))

    def detect_any_button(self, image: bytes, names: list[str],
                          roi_key: str | None = "buttons") -> bool:
        """同步快路径: names 里任一按钮模板是否命中(供 states detector 用)。

        roi_key=None 表示全图搜(「继续」「开始游戏」等位置不固定的按钮);
        否则按 PX[roi_key] 限定区域。

        阈值故意低于 recognize(0.78 vs 0.85/0.88):detector 只负责「是否路由到
        该状态」, 应宽松不漏; 进状态后 handler 内的 recognize 会用精确阈值把关,
        误路由的会因 recognize 无命中而返回空动作。
        """
        matcher = self._matchers.get("buttons")
        if matcher is None:
            return False
        img = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return False
        h, w = img.shape[:2]
        if roi_key:
            x1, y1, x2, y2 = self.PX[roi_key]
            roi = (x1 / w, y1 / h, x2 / w, y2 / h)
        else:
            roi = (0.0, 0.0, 1.0, 1.0)   # 全图(_match_inline 不接受 None)
        return any(matcher._match_inline(image, roi, n, 0.78) for n in names)

    def unlock_landmark(self) -> None:
        """解锁 landlord 标志/底牌缓存(新一局开始时调用, 重新识别)。

        landlord 标志和底牌每局不同(谁地主/3 张底牌), 点「继续」/「开始游戏」
        进入新一局时必须解锁, 否则会沿用上局缓存。
        """
        self._landlord_locked = False
        self._landlord_cards_locked = False
        self._cached_is_landlord = False
        self._cached_landlord_cards = []

    @staticmethod
    def _parse_card_matches(
        matches: list[dict],
    ) -> tuple[list[str], dict[str, list[tuple[int, int]]]]:
        """模板匹配 → 点数列表 + 位置表(颜色剥离, 同点数多张按列排序)。"""
        cards: list[str] = []
        positions: dict[str, list[tuple[int, int]]] = {}
        for m in matches:
            rank = _strip_color(m["template"])
            if rank is None:
                continue
            cx = m["x"] + m["w"] // 2
            cy = m["y"] + m["h"] // 2
            cards.append(rank)
            positions.setdefault(rank, []).append((cx, cy))
        for rank in positions:
            positions[rank].sort(key=lambda p: p[0])
        return cards, positions

"""TFT 中层动作 —— 把游戏语义操作编译成声明式 list[Action]。

每个方法返回 list[Action] (归一化 [0-1000] 坐标):
  - 真机: BaseInput (HDC/scrcpy) 执行这些 Action
  - 回放: 不执行, 只画 (tap=圆点, drag/swipe=从→到箭头)

坐标来源:
  - 按钮/商店槽/出售区: rois.yaml (归一化 [0-1]) → unit_to_1000
  - 棋子位置: perception 给的像素坐标 → to_normalized
TftActions 持有 rois + 分辨率, 屏蔽掉坐标换算细节。
"""

from __future__ import annotations

from gameauto.core.orchestration.base import Action
from gameauto.utils.coordinate import to_normalized, unit_to_1000


class TftActions:
    """中层动作生成器。rois 来自 rois.yaml, (w,h) 为当前帧分辨率。"""

    def __init__(self, rois: dict, width: int, height: int) -> None:
        self.rois = rois or {}
        self.w = int(width)
        self.h = int(height)

    # ── 坐标换算 helpers ──────────────────────────────────────────────

    def _roi_mid1000(self, roi01: tuple[float, float, float, float]) -> tuple[int, int]:
        """[0-1] ROI → 中心点的归一化 [0-1000] 坐标。"""
        l, t, r, b = roi01
        mx = (l + r) / 2
        my = (t + b) / 2
        return unit_to_1000(mx), unit_to_1000(my)

    def _px_mid1000(self, px_box: tuple[int, int, int, int]) -> tuple[int, int]:
        """像素框 (x1,y1,x2,y2) → 中心点的归一化 [0-1000] 坐标。"""
        x1, y1, x2, y2 = px_box
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        return to_normalized(cx, cy, self.w, self.h)

    def _resolve_roi(self, candidates: list[tuple[str, str | None]]) -> tuple[float, float, float, float] | None:
        """按优先级在 rois 里找 ROI。每项 (section, key): key=None 表示 section 本身是 ROI。"""
        for section, key in candidates:
            if key is None:
                box = self.rois.get(section) if isinstance(self.rois, dict) else None
            else:
                sec = self.rois.get(section) if isinstance(self.rois, dict) else None
                box = sec.get(key) if sec else None
            if not box or not isinstance(box, dict):
                continue
            try:
                return (float(box["left"]), float(box["top"]),
                        float(box["right"]), float(box["bottom"]))
            except (KeyError, TypeError, ValueError):
                continue
        return None

    # ── 按钮类 (坐标来自 rois, [0-1]) ─────────────────────────────────

    def refresh(self) -> list[Action]:
        """刷新商店。"""
        roi = self._resolve_roi([("shop", "refresh_btn"), ("template_match", "refresh_btn")])
        if not roi:
            return []
        x, y = self._roi_mid1000(roi)
        return [Action(type="tap", x1=x, y1=y, description="刷新商店")]

    def buy_xp(self) -> list[Action]:
        """购买经验/升级。"""
        roi = self._resolve_roi([("shop", "buy_xp_btn"), ("template_match", "buy_xp_btn")])
        if not roi:
            return []
        x, y = self._roi_mid1000(roi)
        return [Action(type="tap", x1=x, y1=y, description="购买经验")]

    def buy_shop_slot(self, idx: int) -> list[Action]:
        """买商店第 idx 格 (0-4)。优先 ocr:shopN, 回退 shop.slots[N]。"""
        roi = self._resolve_roi([("ocr", f"shop{idx}")])
        if roi is None:
            slots = self.rois.get("shop", {}).get("slots") if isinstance(self.rois, dict) else None
            if slots and idx < len(slots):
                s = slots[idx]
                try:
                    roi = (float(s["left"]), float(s["top"]), float(s["right"]), float(s["bottom"]))
                except (KeyError, TypeError, ValueError):
                    roi = None
        if not roi:
            return []
        x, y = self._roi_mid1000(roi)
        return [Action(type="tap", x1=x, y1=y, description=f"买商店{idx}")]

    # ── 棋子操作 (位置来自 perception, 像素) ──────────────────────────

    def click_champion(self, pos_px: tuple[int, int]) -> list[Action]:
        """点击棋子 (血条下方点击点), 用于弹面板看名字/属性。"""
        x, y = to_normalized(pos_px[0], pos_px[1], self.w, self.h)
        return [Action(type="tap", x1=x, y1=y, description="点击棋子")]

    def sell_champion(self, pos_px: tuple[int, int]) -> list[Action]:
        """出售棋子: 长按棋子(~1s) + 垂直拖到屏幕底部。无独立出售区。

        TFT 手游的出售手势: 按住棋子, 往下拖到屏幕底, 松手即卖。
        duration_ms=1000 体现长按; 终点 x 不变、y 到接近底部(归一化 990)。
        """
        x1, y1 = to_normalized(pos_px[0], pos_px[1], self.w, self.h)
        return [Action(type="drag", x1=x1, y1=y1, x2=x1, y2=990, duration_ms=1000,
                       description="出售棋子(长按拖到底)")]

    def equip(self, item_pos_px: tuple[int, int], champ_pos_px: tuple[int, int]) -> list[Action]:
        """给棋子上装备: 从装备位置拖到棋子 (装备位置来自 perception)。"""
        x1, y1 = to_normalized(item_pos_px[0], item_pos_px[1], self.w, self.h)
        x2, y2 = to_normalized(champ_pos_px[0], champ_pos_px[1], self.w, self.h)
        return [Action(type="drag", x1=x1, y1=y1, x2=x2, y2=y2, duration_ms=400,
                       description="上装备")]

    def equip_from_slot(self, slot_idx: int, champ_pos_px: tuple[int, int]) -> list[Action]:
        """从标注的固定装备槽拖到棋子 (装备识别能力未接入前的过渡方案)。

        装备槽位置用标注工具画成 ocr:itemN (N=0,1,2...), 没标则返回空。
        """
        item_roi = self._resolve_roi([("ocr", f"item{slot_idx}"),
                                      ("items", f"item{slot_idx}")])
        if not item_roi:
            return []
        ix, iy = self._roi_mid1000(item_roi)
        cx, cy = to_normalized(champ_pos_px[0], champ_pos_px[1], self.w, self.h)
        return [Action(type="drag", x1=ix, y1=iy, x2=cx, y2=cy, duration_ms=400,
                       description=f"装备槽{slot_idx}→棋子")]

    def move_champion(self, from_px: tuple[int, int], to_px: tuple[int, int]) -> list[Action]:
        """调整站位 / 捡掉落物: 从一个棋盘格拖到另一个。"""
        x1, y1 = to_normalized(from_px[0], from_px[1], self.w, self.h)
        x2, y2 = to_normalized(to_px[0], to_px[1], self.w, self.h)
        return [Action(type="drag", x1=x1, y1=y1, x2=x2, y2=y2, duration_ms=400,
                       description="移动棋子")]

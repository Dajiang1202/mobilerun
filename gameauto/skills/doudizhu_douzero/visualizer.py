"""Lightweight visualization for DouDiZhu DouZero — card/button annotations.

坐标约定: perception/Action 的坐标都是归一化 [0-1000], 绘制时转回像素。
文字带黑色描边(高对比可读), 牌只标点数, 并画出各识别区(ROI)框。
"""

from __future__ import annotations

import io
import logging
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from gameauto.core.orchestration.base import Action
from gameauto.skills.doudizhu_douzero.perception import DouDiZhuDouzeroPerception as _P

logger = logging.getLogger("gameauto.doudizhu_douzero")

# 识别区像素 ROI(与 perception.PX 同源, 自动同步)
_PX = _P.PX


def _font(size: int = 30, bold: bool = True) -> Any:
    paths = (["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf"]
             if bold else
             ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"])
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def _text(draw, font, xy, text, fill, stroke=(0, 0, 0)):
    """带黑色描边的文字, 任意背景上都清晰可读。"""
    draw.text(xy, text, fill=fill, font=font, stroke_width=2, stroke_fill=stroke)


def _draw_rois(draw, w, h):
    """画出各识别区(灰色框 + 标签)。PX 是 native 像素, 按图尺寸缩放适配 scale=2 等。"""
    font = _font(20)
    nw = max((x2 for _, (_, _, x2, _) in _PX.items()), default=w)
    nh = max((y2 for _, (_, _, _, y2) in _PX.items()), default=h)
    sx, sy = w / nw, h / nh
    for name, (x1, y1, x2, y2) in _PX.items():
        draw.rectangle([x1 * sx, y1 * sy, x2 * sx, y2 * sy], outline=(170, 170, 170), width=2)
        _text(draw, font, (x1 * sx + 4, y1 * sy + 4), f"ROI:{name}", (170, 170, 170))


def annotate_perception(image: bytes, perception: dict) -> bytes:
    """Draw detected cards (green, 点数) + buttons (red) + ROI regions."""
    try:
        img = Image.open(io.BytesIO(image))
        w, h = img.size
        draw = ImageDraw.Draw(img)
        font = _font(32)

        _draw_rois(draw, w, h)

        # 手牌: 绿框 + 点数(只标是什么牌)
        for name, plist in perception.get("card_positions", {}).items():
            for cx, cy in plist:
                px, py = cx / 1000 * w, cy / 1000 * h
                draw.rectangle([px - 22, py - 30, px + 22, py + 30], outline=(0, 255, 0), width=3)
                _text(draw, font, (px - 12, py - 34), name, (0, 255, 0))

        # 按钮: 红圈 + 文字
        for btn in perception.get("buttons", []):
            px, py = btn["x"] / 1000 * w, btn["y"] / 1000 * h
            draw.ellipse([px - 22, py - 22, px + 22, py + 22], outline=(255, 0, 0), width=3)
            _text(draw, font, (px + 26, py - 18), btn.get("text", ""), (255, 0, 0))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        logger.warning("Failed to annotate perception", exc_info=True)
        return image


def annotate_actions(image: bytes, actions: list[Action]) -> bytes:
    """Draw numbered click markers (blue, 加粗) for action sequence."""
    try:
        img = Image.open(io.BytesIO(image))
        w, h = img.size
        draw = ImageDraw.Draw(img)
        font = _font(36)

        for i, action in enumerate(actions):
            if action.type in ("tap", "swipe"):
                px, py = action.x1 / 1000 * w, action.y1 / 1000 * h
                draw.ellipse([px - 26, py - 26, px + 26, py + 26], outline=(50, 120, 255), width=4)
                _text(draw, font, (px - 10, py - 24), str(i + 1), (50, 120, 255))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        logger.warning("Failed to annotate actions", exc_info=True)
        return image

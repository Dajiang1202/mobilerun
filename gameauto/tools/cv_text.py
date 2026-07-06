"""cv2 中英文混排文本绘制 —— cv2.putText 不支持中文 (渲染成 ??), 用 PIL 兜底。

用法:
    from gameauto.tools.cv_text import put_text_zh
    img = put_text_zh(img, "金币:42 娑娜", (10, 10), color_bgr=(0,255,0), px=22)
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

_FONT_PATH: str | None = None
_FONTS: dict[int, ImageFont.FreeTypeFont] = {}


def _find_font() -> str | None:
    global _FONT_PATH
    if _FONT_PATH is not None:
        return _FONT_PATH
    for p in [
        "C:/Windows/Fonts/msyh.ttc",     # 微软雅黑
        "C:/Windows/Fonts/msyh.ttf",
        "C:/Windows/Fonts/simhei.ttf",   # 黑体
        "C:/Windows/Fonts/yahei.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",  # Linux 兜底
    ]:
        if Path(p).exists():
            _FONT_PATH = p
            return p
    _FONT_PATH = ""  # 没找到, 用 PIL 默认 (中文仍会缺字, 但不报错)
    return None


def _get_font(px: int) -> ImageFont.FreeTypeFont:
    px = max(8, px)
    if px not in _FONTS:
        path = _find_font()
        try:
            _FONTS[px] = ImageFont.truetype(path, px) if path else ImageFont.load_default()
        except Exception:  # noqa: BLE001
            _FONTS[px] = ImageFont.load_default()
    return _FONTS[px]


def put_text_zh(
    img: np.ndarray,
    text: str,
    org: tuple[int, int],
    color_bgr: tuple[int, int, int] = (0, 255, 0),
    px: int = 22,
) -> np.ndarray:
    """在 BGR 图上画中英文混排文本, 返回新图 (PIL 往返, 别忘了接返回值)。

    Args:
        img: BGR ndarray.
        text: 文本 (可含中文).
        org: (x, y) 左上角坐标.
        color_bgr: BGR 颜色.
        px: 字号 (像素).
    """
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil)
    rgb = (int(color_bgr[2]), int(color_bgr[1]), int(color_bgr[0]))
    draw.text(org, str(text), font=_get_font(px), fill=rgb)
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def overlay_text(
    img: np.ndarray,
    text: str,
    org: tuple[int, int],
    color_bgr: tuple[int, int, int] = (255, 255, 255),
    px: int = 22,
    bg: tuple[int, int, int] = (0, 0, 0),
    bg_alpha: float = 0.5,
    pad: int = 5,
) -> np.ndarray:
    """画文字 + 仅在文字背后垫一块半透明底 (不全宽遮挡), 底下内容透得见。

    替代 cv2.rectangle 整条不透明信息条 —— 避免盖住图顶的 stage/timer/HP。
    """
    x, y = org
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).convert("RGBA")
    font = _get_font(px)
    try:
        l, t, r, b = font.getbbox(str(text))
        tw, th = r - l, b - t
    except Exception:  # noqa: BLE001
        tw, th = px * len(str(text)) // 2, px
    ov = Image.new("RGBA", pil.size, (0, 0, 0, 0))
    ImageDraw.Draw(ov).rectangle(
        [x - pad, y - pad, x + tw + pad, y + th + pad * 2],
        fill=(int(bg[2]), int(bg[1]), int(bg[0]), int(255 * bg_alpha)),
    )
    pil = Image.alpha_composite(pil, ov)
    ImageDraw.Draw(pil).text(
        org, str(text), font=font,
        fill=(int(color_bgr[2]), int(color_bgr[1]), int(color_bgr[0])),
    )
    return cv2.cvtColor(np.array(pil.convert("RGB")), cv2.COLOR_RGB2BGR)


def overlay_multi(
    img: np.ndarray,
    items: list[tuple[str, tuple[int, int], tuple[int, int, int], int]],
    bg_alpha: float = 0.5,
    pad: int = 5,
) -> np.ndarray:
    """批量画多段文字 (单次 PIL 往返), 大幅减少 overlay_text 逐条调用的卡顿。

    items: [(text, (x,y), color_bgr, px), ...]
    """
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).convert("RGBA")
    fonts: dict[int, ImageFont.FreeTypeFont] = {}
    # 先画半透明底
    ov = Image.new("RGBA", pil.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for text, (x, y), color_bgr, px in items:
        if px not in fonts:
            fonts[px] = _get_font(px)
        font = fonts[px]
        try:
            l, t, r, b = font.getbbox(str(text))
            tw, th = r - l, b - t
        except Exception:  # noqa: BLE001
            tw, th = px * len(str(text)) // 2, px
        od.rectangle([x - pad, y - pad, x + tw + pad, y + th + pad * 2],
                      fill=(0, 0, 0, int(255 * bg_alpha)))
    pil = Image.alpha_composite(pil, ov)
    # 再画文字
    draw = ImageDraw.Draw(pil)
    for text, (x, y), color_bgr, px in items:
        draw.text((x, y), str(text), font=fonts.get(px) or _get_font(px),
                  fill=(int(color_bgr[2]), int(color_bgr[1]), int(color_bgr[0])))
    return cv2.cvtColor(np.array(pil.convert("RGB")), cv2.COLOR_RGB2BGR)

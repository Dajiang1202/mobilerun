"""Lightweight visualization for DouDiZhu DouZero — card/button annotations."""

from __future__ import annotations

import io
import logging
from typing import Any

from PIL import Image, ImageDraw

from gameauto.core.orchestration.base import Action

logger = logging.getLogger("gameauto.doudizhu_douzero")


def annotate_perception(image: bytes, perception: dict) -> bytes:
    """Draw bounding boxes for detected cards and buttons."""
    try:
        img = Image.open(io.BytesIO(image))
        draw = ImageDraw.Draw(img)

        # Draw card positions: {点数: [(x,y), ...]}(同点数多张)
        card_positions = perception.get("card_positions", {})
        for name, plist in card_positions.items():
            for cx, cy in plist:
                draw.rectangle([cx - 15, cy - 20, cx + 15, cy + 20], outline="green", width=2)
                draw.text((cx - 8, cy - 18), name, fill="green")

        # Draw buttons
        for btn in perception.get("buttons", []):
            x, y = btn["x"], btn["y"]
            draw.ellipse([x - 10, y - 10, x + 10, y + 10], outline="red", width=2)
            draw.text((x + 12, y - 8), btn.get("text", ""), fill="red")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        logger.warning("Failed to annotate perception", exc_info=True)
        return image


def annotate_actions(image: bytes, actions: list[Action]) -> bytes:
    """Draw numbered click markers for action sequence."""
    try:
        img = Image.open(io.BytesIO(image))
        draw = ImageDraw.Draw(img)

        for i, action in enumerate(actions):
            if action.type in ("tap", "swipe"):
                x, y = action.x1, action.y1
                draw.ellipse([x - 12, y - 12, x + 12, y + 12],
                             outline="blue", width=3)
                draw.text((x + 15, y - 10), str(i + 1), fill="blue")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        logger.warning("Failed to annotate actions", exc_info=True)
        return image

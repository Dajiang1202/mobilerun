"""PaddleOCR wrapper — real implementation for text recognition."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import cv2
import numpy as np

from gameauto.core.perception.cv.base import BaseCVTask

logger = logging.getLogger("gameauto.cv.ocr")

# ── PaddleOCR singleton ────────────────────────────────────────────────
_ocr_instance = None


def _get_ocr(lang: str = "ch", use_angle_cls: bool = True, gpu: bool = True):
    """Lazy-init PaddleOCR singleton. First call downloads ~30MB models."""
    global _ocr_instance
    if _ocr_instance is None:
        try:
            from paddleocr import PaddleOCR
        except ImportError:
            raise ImportError(
                "PaddleOCR not installed. Run: pip install paddlepaddle paddleocr"
            )
        _ocr_instance = PaddleOCR(
            lang=lang,
            use_angle_cls=use_angle_cls,
            use_gpu=gpu,
            show_log=False,
        )
        logger.info("PaddleOCR initialized (lang=%s, gpu=%s)", lang, gpu)
    return _ocr_instance


class OcrTask(BaseCVTask):
    """PaddleOCR-based text recognition.

    Usage::

        ocr = OcrTask(lang="ch")
        result = await ocr.run(image_bytes, roi=(0.02, 0.88, 0.08, 0.95))
        # result = {"texts": ["42"], "confidences": [0.98], "combined_text": "42"}

    All OCR runs on a thread pool since PaddleOCR is synchronous.
    """

    def __init__(
        self,
        lang: str = "ch",
        use_angle_cls: bool = True,
        gpu: bool = True,
        warmup: bool = True,
    ) -> None:
        self._lang = lang
        self._use_angle_cls = use_angle_cls
        self._gpu = gpu
        # Optionally warm up OCR during init so model download happens
        # before the main loop, not during the first frame.
        if warmup:
            try:
                _get_ocr(lang=lang, use_angle_cls=use_angle_cls, gpu=gpu)
            except ImportError:
                logger.warning(
                    "PaddleOCR not installed — OCR will fail at runtime. "
                    "Run: pip install paddlepaddle paddleocr"
                )

    async def run(
        self,
        image: bytes,
        roi: tuple[float, float, float, float] | None = None,
        config: dict | None = None,
    ) -> dict[str, Any]:
        """Run OCR on ``image``, optionally cropped to ``roi``.

        Args:
            image: PNG/JPEG screenshot bytes.
            roi: (left, top, right, bottom) normalized [0-1], or None for full image.
            config: Optional overrides (lang, cls, threshold, etc.).

        Returns:
            {"texts": [...], "confidences": [...], "combined_text": "..."}
        """
        cfg = config or {}

        try:
            # Decode image bytes → BGR numpy array
            img_array = np.frombuffer(image, dtype=np.uint8)
            img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if img is None:
                logger.warning("OCR: failed to decode image")
                return {"texts": [], "confidences": [], "combined_text": ""}

            # Crop to ROI if specified
            if roi is not None:
                h, w = img.shape[:2]
                left = max(0, int(roi[0] * w))
                top = max(0, int(roi[1] * h))
                right = min(w, int(roi[2] * w))
                bottom = min(h, int(roi[3] * h))
                if right <= left or bottom <= top:
                    logger.warning(
                        "OCR: invalid ROI crop (%d,%d,%d,%d)", left, top, right, bottom
                    )
                    return {"texts": [], "confidences": [], "combined_text": ""}
                img = img[top:bottom, left:right]

            # Run PaddleOCR in thread pool (synchronous API)
            ocr = _get_ocr(
                lang=cfg.get("lang", self._lang),
                use_angle_cls=cfg.get("use_angle_cls", self._use_angle_cls),
                gpu=cfg.get("gpu", self._gpu),
            )
            raw_result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ocr.ocr(img, cls=True)
            )

            # PaddleOCR returns: [[[box, (text, confidence)], ...], ...] per text block
            texts: list[str] = []
            confidences: list[float] = []
            if raw_result and raw_result[0]:
                for line in raw_result[0]:
                    if len(line) >= 2:
                        text, conf = line[1]
                        threshold = cfg.get("threshold", 0.5)
                        if conf >= threshold and text.strip():
                            texts.append(text.strip())
                            confidences.append(float(conf))

            combined = " ".join(texts)
            return {
                "texts": texts,
                "confidences": confidences,
                "combined_text": combined,
            }

        except Exception:
            logger.exception("OCR task failed")
            return {"texts": [], "confidences": [], "combined_text": ""}

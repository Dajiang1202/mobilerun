"""OpenCV template matching — real implementation for UI element detection."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from gameauto.core.perception.cv.base import BaseCVTask

logger = logging.getLogger("gameauto.cv.template_match")


class TemplateMatchTask(BaseCVTask):
    """OpenCV matchTemplate-based image matching.

    Preloads template images from a directory. Supports multi-scale matching
    and Non-Maximum Suppression for multi-instance detection.

    Usage::

        tm = TemplateMatchTask(template_dir="skills/tft/assets/templates")
        result = await tm.run(image_bytes, roi=(0.35, 0.80, 0.65, 0.95))
        # result = {"matches": [...], "best_score": 0.92}

    Templates should be named like ``<name>.png`` — the stem is used as the
    match label.
    """

    # Scale factors for multi-scale matching
    _SCALES = (0.8, 0.9, 1.0, 1.1, 1.2)

    def __init__(self, template_dir: str | None = None) -> None:
        """Create a template matcher.

        Args:
            template_dir: Directory containing .png template images.
                          If None, templates must be loaded via ``load_templates()``.
        """
        self._templates: dict[str, np.ndarray] = {}
        if template_dir:
            self.load_templates(template_dir)

    def load_templates(self, template_dir: str) -> None:
        """Load all .png files from ``template_dir`` into memory.

        Call this once during setup; templates are cached and reused per frame.
        """
        tpl_path = Path(template_dir)
        if not tpl_path.is_dir():
            logger.warning("Template directory not found: %s", tpl_path)
            return

        for png_file in sorted(tpl_path.glob("*.png")):
            name = png_file.stem
            # 用 imdecode + np.fromfile 替代 cv2.imread, 兼容中文路径/文件名
            # (cv2.imread 在 Windows 读中文路径/文件名会返回 None, 导致按钮模板
            # 如 叫地主.png 加载失败)
            data = np.fromfile(str(png_file), dtype=np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
            if img is not None:
                self._templates[name] = img
                logger.debug("Loaded template: %s (%dx%d)", name, img.shape[1], img.shape[0])
            else:
                logger.warning("Failed to load template: %s", png_file)

        logger.info("Loaded %d templates from %s", len(self._templates), tpl_path)

    def add_template(self, name: str, image: np.ndarray) -> None:
        """Add a single template programmatically."""
        self._templates[name] = image

    @property
    def template_names(self) -> list[str]:
        return list(self._templates.keys())

    async def run(
        self,
        image: bytes,
        roi: tuple[float, float, float, float] | None = None,
        config: dict | None = None,
    ) -> dict[str, Any]:
        """Match all loaded templates against ``image``.

        Args:
            image: PNG/JPEG screenshot bytes.
            roi: (left, top, right, bottom) normalized [0-1] to restrict search area.
            config: Optional overrides — ``threshold`` (float, default 0.80),
                    ``scales`` (list[float]), ``nms_iou`` (float, default 0.3),
                    ``filter_names`` (list[str] — only match these templates).

        Returns:
            {"matches": [{"template": str, "score": float, "x": int, "y": int,
                          "w": int, "h": int}, ...],
             "best_match": str | None,
             "best_score": float}
        """
        cfg = config or {}

        try:
            # Decode image
            img_array = np.frombuffer(image, dtype=np.uint8)
            img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if img is None:
                logger.warning("TemplateMatch: failed to decode image")
                return {"matches": [], "best_match": None, "best_score": 0.0}

            h, w = img.shape[:2]

            # Crop to ROI
            if roi is not None:
                left = max(0, int(roi[0] * w))
                top = max(0, int(roi[1] * h))
                right = min(w, int(roi[2] * w))
                bottom = min(h, int(roi[3] * h))
                if right <= left or bottom <= top:
                    return {"matches": [], "best_match": None, "best_score": 0.0}
                img = img[top:bottom, left:right]
                roi_offset_x, roi_offset_y = left, top
            else:
                roi_offset_x, roi_offset_y = 0, 0

            # Run matching in thread pool
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._match_all(img, roi_offset_x, roi_offset_y, cfg),
            )

        except Exception:
            logger.exception("TemplateMatch task failed")
            return {"matches": [], "best_match": None, "best_score": 0.0}

    def _match_inline(
        self,
        image: bytes,
        roi: tuple[float, float, float, float],
        template_name: str,
        threshold: float = 0.70,
    ) -> bool:
        """Synchronous single-template match for state detection.

        Designed for fast (< 30ms) detectors — no thread pool, no async.
        Returns True if ``template_name`` is found in ``roi`` above ``threshold``.

        Args:
            image: PNG/JPEG screenshot bytes.
            roi: (left, top, right, bottom) normalized [0-1].
            template_name: Key in self._templates dict.
            threshold: Minimum TM_CCOEFF_NORMED score.
        """
        try:
            tpl = self._templates.get(template_name)
            if tpl is None:
                return False

            img_array = np.frombuffer(image, dtype=np.uint8)
            img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if img is None:
                return False

            h, w = img.shape[:2]

            # Crop to ROI
            left = max(0, int(roi[0] * w))
            top = max(0, int(roi[1] * h))
            right = min(w, int(roi[2] * w))
            bottom = min(h, int(roi[3] * h))
            if right <= left or bottom <= top:
                return False
            crop = img[top:bottom, left:right]

            # Multi-scale match
            tpl_h, tpl_w = tpl.shape[:2]
            best = 0.0
            for scale in self._SCALES:
                sw = max(1, int(tpl_w * scale))
                sh = max(1, int(tpl_h * scale))
                if sw > crop.shape[1] or sh > crop.shape[0]:
                    continue
                scaled = cv2.resize(tpl, (sw, sh))
                result = cv2.matchTemplate(crop, scaled, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(result)
                if max_val > best:
                    best = max_val

            return best >= threshold

        except Exception:
            return False

    def _match_all(
        self,
        img: np.ndarray,
        offset_x: int,
        offset_y: int,
        cfg: dict,
    ) -> dict[str, Any]:
        """Synchronous matching core — runs in thread pool."""
        threshold = cfg.get("threshold", 0.80)
        scales = cfg.get("scales", self._SCALES)
        nms_iou = cfg.get("nms_iou", 0.3)
        filter_names: list[str] | None = cfg.get("filter_names")

        all_matches: list[dict[str, Any]] = []
        best_match_name: str | None = None
        best_score: float = 0.0

        for tpl_name, tpl_img in self._templates.items():
            if filter_names and tpl_name not in filter_names:
                continue

            tpl_h, tpl_w = tpl_img.shape[:2]
            # 注: 不在此处用「原模板尺寸」过滤 —— scale<1(如 scrcpy scale=2, 模板÷2)时,
            # 原模板可能 > 搜索区(scale=2 的 crop 偏小), 但缩放后能匹配。
            # 交给下面 scale 循环内的缩放后尺寸检查(line 225)判断。
            tpl_matches: list[dict[str, Any]] = []

            for scale in scales:
                new_w = max(1, int(tpl_w * scale))
                new_h = max(1, int(tpl_h * scale))
                if new_w > img.shape[1] or new_h > img.shape[0]:
                    continue

                scaled_tpl = cv2.resize(tpl_img, (new_w, new_h))

                try:
                    result = cv2.matchTemplate(img, scaled_tpl, cv2.TM_CCOEFF_NORMED)
                except cv2.error:
                    continue

                # Find all matches above threshold
                locations = np.where(result >= threshold)
                for pt in zip(*locations[::-1]):
                    score = float(result[pt[1], pt[0]])
                    tpl_matches.append({
                        "template": tpl_name,
                        "score": score,
                        "x": pt[0] + offset_x,
                        "y": pt[1] + offset_y,
                        "w": new_w,
                        "h": new_h,
                        "scale": scale,
                    })

            # NMS within same template
            tpl_matches.sort(key=lambda m: m["score"], reverse=True)
            kept = self._nms(tpl_matches, nms_iou)
            all_matches.extend(kept)

            if kept and kept[0]["score"] > best_score:
                best_score = kept[0]["score"]
                best_match_name = kept[0]["template"]

        # Sort final results by score descending
        all_matches.sort(key=lambda m: m["score"], reverse=True)

        return {
            "matches": all_matches,
            "best_match": best_match_name,
            "best_score": best_score,
        }

    @staticmethod
    def _nms(
        matches: list[dict[str, Any]],
        iou_threshold: float,
    ) -> list[dict[str, Any]]:
        """Non-Maximum Suppression: keep highest-score match, suppress overlapping ones."""
        if not matches:
            return []

        kept: list[dict[str, Any]] = []
        suppressed = set()

        for i, m in enumerate(matches):
            if i in suppressed:
                continue
            kept.append(m)

            for j in range(i + 1, len(matches)):
                if j in suppressed:
                    continue
                iou = _box_iou(
                    m["x"], m["y"], m["w"], m["h"],
                    matches[j]["x"], matches[j]["y"], matches[j]["w"], matches[j]["h"],
                )
                if iou > iou_threshold:
                    suppressed.add(j)

        return kept


def _box_iou(
    x1: int, y1: int, w1: int, h1: int,
    x2: int, y2: int, w2: int, h2: int,
) -> float:
    """Intersection-over-Union for two axis-aligned boxes."""
    ix1 = max(x1, x2)
    iy1 = max(y1, y2)
    ix2 = min(x1 + w1, x2 + w2)
    iy2 = min(y1 + h1, y2 + h2)

    inter_w = max(0, ix2 - ix1)
    inter_h = max(0, iy2 - iy1)
    inter_area = inter_w * inter_h

    area1 = w1 * h1
    area2 = w2 * h2
    union_area = area1 + area2 - inter_area

    return inter_area / union_area if union_area > 0 else 0.0

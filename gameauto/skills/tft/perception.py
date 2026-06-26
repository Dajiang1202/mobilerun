"""TFT 感知层 — 组装 OCR + 模板匹配 Pipeline，不调 VLM。

在 PLANNING 状态下运行，并行采集：金币、等级、血量、计时器、
商店 5 个 slot 的棋子名、备战席棋子、回合文本。

新增:
- capture_scale 参数（对应 scrcpy scale，ROIs 已归一化故不影响 ROI 计算）
- buttons_only 模式（轻量状态检测，跳过全量 OCR）
- 商店灰色检测（HSV: S<30 → 买不起的棋子）
- stage_text OCR（读取 "3-5" 等回合文本）
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from gameauto.core.perception.base import BasePerception, PerceptionResult, PerceptionTask
from gameauto.core.perception.cv.ocr import OcrTask
from gameauto.core.perception.cv.template_match import TemplateMatchTask
from gameauto.core.perception.pipeline import PerceptionPipeline

logger = logging.getLogger("gameauto.tft.perception")

# ── Default ROIs (fallback when rois.yaml is missing) ──────────────────
_DEFAULT_ROIS: dict[str, Any] = {
    "info": {
        "gold":          (0.02, 0.89, 0.10, 0.95),
        "level":         (0.02, 0.02, 0.08, 0.07),
        "hp":            (0.86, 0.02, 0.98, 0.07),
        "round_timer":   (0.44, 0.00, 0.56, 0.06),
        "stage_text":    (0.40, 0.00, 0.60, 0.06),  # "3-5" 回合文本
    },
    "shop": {
        "slots": [
            (0.06, 0.65, 0.22, 0.96),
            (0.24, 0.65, 0.40, 0.96),
            (0.42, 0.65, 0.58, 0.96),
            (0.60, 0.65, 0.76, 0.96),
            (0.78, 0.65, 0.94, 0.96),
        ],
    },
    "bench": (0.04, 0.80, 0.96, 0.90),
    "state_detection": {
        "lobby_play_btn":    (0.35, 0.82, 0.65, 0.93),
        "loading_indicator": (0.40, 0.38, 0.60, 0.55),
        "planning_timer":    (0.44, 0.00, 0.56, 0.06),
        "combat_indicator":  (0.00, 0.00, 0.18, 0.08),
        "result_rank":       (0.30, 0.10, 0.70, 0.40),  # 结算排名区域
    },
    "buttons": {
        "refresh_btn": (0.88, 0.66, 0.95, 0.72),
        "buy_xp_btn":  (0.88, 0.74, 0.95, 0.80),
    },
}


def _load_rois(rois_path: str | None = None) -> dict[str, Any]:
    """Load ROI definitions from YAML, falling back to defaults."""
    if rois_path and Path(rois_path).exists():
        with open(rois_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return _DEFAULT_ROIS


class TftPerception(BasePerception):
    """金铲铲之战感知器 — 纯 CV Pipeline，不调 VLM。

    用法::

        ocr = OcrTask(lang="ch")
        tm = TemplateMatchTask("skills/tft/assets/templates")
        perception = TftPerception(ocr, tm, capture_scale=2)

        result = await perception.recognize(screenshot_bytes)
        # result.parsed = {
        #     "gold": 42, "level": 5, "hp": 78, "timer_seconds": 22,
        #     "shop_units": ["盖伦", None, "孙悟空", None, None],
        #     "gray_slots": [False, True, False, False, True],
        #     "stage_text": "3-5",
        #     "bench_text": "...",
        # }
    """

    def __init__(
        self,
        ocr_task: OcrTask,
        tm_task: TemplateMatchTask | None = None,
        rois_path: str | None = None,
        capture_scale: int = 1,
    ) -> None:
        self._ocr = ocr_task
        self._tm = tm_task
        self._rois = _load_rois(rois_path)
        self._capture_scale = capture_scale

    def get_state_detection_roi(self, state_key: str) -> tuple[float, float, float, float]:
        """Get the ROI for a state detection template match.

        Args:
            state_key: One of 'lobby_play_btn', 'loading_indicator',
                       'planning_timer', 'combat_indicator', 'result_rank'.

        Returns:
            (left, top, right, bottom) normalized [0-1].
        """
        return tuple(
            self._rois.get("state_detection", {}).get(
                state_key,
                _DEFAULT_ROIS["state_detection"].get(state_key, (0, 0, 1, 1)),
            )
        )

    def get_button_roi(self, button_key: str) -> tuple[float, float, float, float]:
        """Get ROI for a UI button."""
        return tuple(
            self._rois.get("buttons", {}).get(
                button_key,
                _DEFAULT_ROIS["buttons"].get(button_key, (0, 0, 1, 1)),
            )
        )

    async def recognize(
        self, image: bytes, buttons_only: bool = False,
    ) -> PerceptionResult:
        """Run perception pipeline on a screenshot.

        Args:
            image: PNG screenshot bytes.
            buttons_only: If True, only run lightweight state detection
                          (skip full OCR). Used for non-PLANNING states.

        Returns:
            PerceptionResult with parsed dict containing gold, level, hp,
            timer, shop_units, gray_slots, stage_text, bench_text.
        """
        if buttons_only:
            return await self._recognize_lightweight(image)

        return await self._recognize_full(image)

    async def _recognize_lightweight(self, image: bytes) -> PerceptionResult:
        """轻量感知: 只做状态检测，跳过全量 OCR。

        用于非 PLANNING 状态（COMBAT/LOBBY/RESULT/LOADING），
        节省 OCR 耗时（~100ms）。
        """
        # 轻量模式只返回空 parsed，状态检测由 detector 完成
        return PerceptionResult(
            raw_response=None,
            parsed={},
            tasks_output={},
            latency_ms=0,
        )

    async def _recognize_full(self, image: bytes) -> PerceptionResult:
        """全量感知: 并行 OCR 所有区域 + 灰色检测。"""
        pipeline = PerceptionPipeline(vlm_client=None)

        # Register OCR runner
        async def _ocr_runner(img: bytes, task: PerceptionTask) -> dict:
            return await self._ocr.run(img, task.roi, task.config)

        pipeline.register_custom_runner("ocr", _ocr_runner)

        # Register template_match runner (if available)
        if self._tm:

            async def _tm_runner(img: bytes, task: PerceptionTask) -> dict:
                return await self._tm.run(img, task.roi, task.config)

            pipeline.register_custom_runner("template_match", _tm_runner)

        # ── Add info OCR tasks ─────────────────────────────────────────
        info = self._rois.get("info", _DEFAULT_ROIS["info"])
        pipeline.add_task(PerceptionTask(
            name="gold", task_type="ocr", roi=info.get("gold"),
            config={"threshold": 0.6},
        ))
        pipeline.add_task(PerceptionTask(
            name="level", task_type="ocr", roi=info.get("level"),
            config={"threshold": 0.6},
        ))
        pipeline.add_task(PerceptionTask(
            name="hp", task_type="ocr", roi=info.get("hp"),
            config={"threshold": 0.6},
        ))
        pipeline.add_task(PerceptionTask(
            name="timer", task_type="ocr", roi=info.get("round_timer"),
            config={"threshold": 0.6},
        ))

        # stage_text OCR（读取 "3-5" 等回合文本）
        stage_roi = info.get("stage_text", info.get("round_timer"))
        if stage_roi:
            pipeline.add_task(PerceptionTask(
                name="stage_text", task_type="ocr", roi=stage_roi,
                config={"threshold": 0.5},
            ))

        # ── Add shop slot OCR tasks ────────────────────────────────────
        shop_cfg = self._rois.get("shop", _DEFAULT_ROIS["shop"])
        shop_slots = shop_cfg.get("slots", _DEFAULT_ROIS["shop"]["slots"])
        for i, slot_roi in enumerate(shop_slots):
            pipeline.add_task(PerceptionTask(
                name=f"shop_slot_{i}",
                task_type="ocr",
                roi=tuple(slot_roi),
                config={"threshold": 0.4},
            ))

        # ── Add bench OCR task ─────────────────────────────────────────
        bench_roi = self._rois.get("bench", _DEFAULT_ROIS["bench"])
        pipeline.add_task(PerceptionTask(
            name="bench",
            task_type="ocr",
            roi=tuple(bench_roi),
            config={"threshold": 0.4},
        ))

        # ── Run pipeline ───────────────────────────────────────────────
        result = await pipeline.run(image)

        # ── Parse & merge ──────────────────────────────────────────────
        parsed = self._parse_results(result.tasks_output)

        # ── 灰色检测（需要原始图像 BGR）────────────────────────────────
        gray_slots = self._detect_gray_slots(image, shop_slots)
        parsed["gray_slots"] = gray_slots

        result.parsed = parsed

        return result

    # ── Gray slot detection ────────────────────────────────────────────

    def _detect_gray_slots(
        self,
        image_bytes: bytes,
        shop_slots: list[tuple[float, float, float, float]],
    ) -> list[bool]:
        """检测商店 5 个 slot 是否灰色（买不起）。

        HSV: S<30 且 V<150 → 灰色。70%+ 像素为灰色 → 该 slot 买不起。
        参考斗地主颜色校验模式（_is_red 红/黑区分）。

        Args:
            image_bytes: PNG screenshot bytes.
            shop_slots: List of normalized ROI tuples for each shop slot.

        Returns:
            List of 5 booleans: True = gray (can't afford), False = available.
        """
        try:
            arr = np.frombuffer(image_bytes, dtype=np.uint8)
            img_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img_bgr is None:
                return [False] * len(shop_slots)

            h, w = img_bgr.shape[:2]
            gray_flags: list[bool] = []

            for slot_roi in shop_slots:
                l, t, r, b = slot_roi
                # Crop slot region (ROIs are normalized [0-1])
                x1, y1 = int(l * w), int(t * h)
                x2, y2 = int(r * w), int(b * h)
                slot_img = img_bgr[y1:y2, x1:x2]

                if slot_img.size == 0:
                    gray_flags.append(False)
                    continue

                # Convert to HSV
                hsv = cv2.cvtColor(slot_img, cv2.COLOR_BGR2HSV)
                # 低饱和度 = 灰色
                saturation = hsv[:, :, 1]
                gray_ratio = (saturation < 30).mean()

                gray_flags.append(bool(gray_ratio > 0.7))

            return gray_flags

        except Exception:
            logger.debug("Gray slot detection failed", exc_info=True)
            return [False] * len(shop_slots)

    # ── Result parsing ─────────────────────────────────────────────────

    def _parse_results(self, tasks_output: dict[str, Any]) -> dict[str, Any]:
        """Extract structured game data from raw OCR outputs."""
        parsed: dict[str, Any] = {}

        # Parse numeric fields
        parsed["gold"] = _parse_int(tasks_output, "gold")
        parsed["level"] = _parse_int(tasks_output, "level")
        parsed["hp"] = _parse_int(tasks_output, "hp")
        parsed["timer_seconds"] = _parse_int(tasks_output, "timer")

        # Parse stage text (e.g. "3-5", "Stage 3 Round 5")
        parsed["stage_text"] = _parse_stage_text(tasks_output)

        # Parse shop slots — champion names
        shop_units: list[str | None] = []
        for i in range(5):
            key = f"shop_slot_{i}"
            raw = tasks_output.get(key)
            text = raw.get("combined_text", "") if isinstance(raw, dict) else ""
            # Filter out non-champion text (e.g., "购买", "刷新", gold numbers)
            cleaned = _clean_champion_name(text)
            shop_units.append(cleaned if cleaned else None)
        parsed["shop_units"] = shop_units

        # Parse bench
        bench_raw = tasks_output.get("bench")
        bench_text = bench_raw.get("combined_text", "") if isinstance(bench_raw, dict) else ""
        parsed["bench_text"] = bench_text

        return parsed


# ── Helpers ────────────────────────────────────────────────────────────

def _parse_int(tasks_output: dict[str, Any], key: str) -> int | None:
    """Extract integer from OCR task output."""
    raw = tasks_output.get(key)
    if not isinstance(raw, dict):
        return None
    text = raw.get("combined_text", "")
    # Extract first number from text (e.g. "金币 42" → 42, "LV.5" → 5)
    match = re.search(r"(\d+)", str(text))
    if match:
        return int(match.group(1))
    return None


def _parse_stage_text(tasks_output: dict[str, Any]) -> str | None:
    """Extract stage-round text from OCR (e.g. '3-5', 'Stage 3-5').

    Looks for patterns like '3-5', '3−5'(em dash), '3 5'.
    """
    raw = tasks_output.get("stage_text")
    if not isinstance(raw, dict):
        return None
    text = raw.get("combined_text", "").strip()
    if not text:
        return None

    # Try to find "N-N" pattern (with possible em dash or space)
    match = re.search(r"(\d+)\s*[-−–—]\s*(\d+)", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}"

    # Try two separate numbers
    numbers = re.findall(r"\d+", text)
    if len(numbers) >= 2:
        return f"{numbers[0]}-{numbers[1]}"

    return None


def _clean_champion_name(text: str) -> str | None:
    """Filter OCR output to extract a plausible champion name.

    Returns None if text looks like UI noise (numbers only, common UI words).
    """
    text = text.strip()
    if not text or len(text) < 1:
        return None

    # Filter out purely numeric outputs
    if re.match(r"^[\d\s./]+$", text):
        return None

    # Filter out common UI noise words
    noise_words = {"购买", "刷新", "出售", "经验", "升级", "金币", "锁定"}
    if text in noise_words:
        return None

    # Remove common OCR artifacts
    text = re.sub(r"[|l1I{}[\]\\]", "", text).strip()

    return text if text else None

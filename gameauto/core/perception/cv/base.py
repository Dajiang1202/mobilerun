"""Base CV task interface — M1 skeleton, full implementation in M2."""

from abc import ABC, abstractmethod
from typing import Any


class BaseCVTask(ABC):
    """Abstract CV task — OCR, template matching, YOLO, etc.

    Each implementation handles a specific CV method. Tasks are
    executed by PerceptionPipeline.run() in parallel with other tasks.
    """

    @abstractmethod
    async def run(self, image: bytes, roi: tuple[float, float, float, float] | None, config: dict) -> Any:
        """Process image and return detection results.

        Args:
            image: Full screenshot bytes.
            roi: (left, top, right, bottom) in normalized [0.0-1.0], or None for full image.
            config: Task-specific parameters (thresholds, model names, etc.).

        Returns:
            Task-specific result (e.g. OCR text, template match scores, YOLO detections).
        """
        ...

"""PaddleOCR wrapper — M1 skeleton, M2 full implementation."""

from gameauto.core.perception.cv.base import BaseCVTask


class OcrTask(BaseCVTask):
    """PaddleOCR-based text recognition.

    M1: skeleton — returns empty result.
    M2: full PaddleOCR v4 integration with GPU acceleration.
    """

    async def run(self, image, roi=None, config=None):
        # M2: implement PaddleOCR pipeline
        return {"text": "", "confidence": 0.0, "regions": []}

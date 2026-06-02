"""OpenCV template matching — M1 skeleton, M2 full implementation."""

from gameauto.core.perception.cv.base import BaseCVTask


class TemplateMatchTask(BaseCVTask):
    """OpenCV matchTemplate-based image matching.

    M1: skeleton — returns empty result.
    M2: full implementation with multi-template, multi-instance, sub-pixel refinement.
    """

    async def run(self, image, roi=None, config=None):
        # M2: implement template matching pipeline
        return {"matches": [], "best_score": 0.0}

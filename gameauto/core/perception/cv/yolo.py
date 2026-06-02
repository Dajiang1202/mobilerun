"""YOLO ONNX inference — M1 skeleton, M2 full implementation."""

from gameauto.core.perception.cv.base import BaseCVTask


class YoloTask(BaseCVTask):
    """YOLO-based object detection via ONNX Runtime.

    M1: skeleton — returns empty result.
    M2: full YOLOv8 ONNX inference with GPU acceleration.
    """

    async def run(self, image, roi=None, config=None):
        # M2: implement YOLO ONNX pipeline
        return {"detections": []}

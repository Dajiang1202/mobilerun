"""Standalone H.264 decoder using PyAV.

Can be used independently of the JPype bridge for testing or
offline decoding of recorded H.264 streams.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from av import VideoFrame
    from av.codec import CodecContext
    from PIL.Image import Image

log = logging.getLogger("gameauto.capture.scrcpy.decoder")


class H264Decoder:
    """Decode H.264 NAL unit byte streams into PIL Images.

    Usage::

        decoder = H264Decoder()
        for nal in nal_units:
            for img in decoder.feed(nal):
                process(img)
    """

    def __init__(self) -> None:
        self._codec: CodecContext | None = None
        self._resolution: tuple[int, int] | None = None

    def feed(self, nal_data: bytes) -> list[Image]:
        """Feed raw H.264 bytes, return newly decoded PIL Images."""
        if self._codec is None:
            import av
            codec = av.CodecContext.create("h264", "r")
            codec.options["threads"] = "auto"
            codec.options["delay"] = "0"
            self._codec = codec

        frames: list[Image] = []
        try:
            for packet in self._codec.parse(nal_data):
                for frame in self._codec.decode(packet):
                    img = frame.to_image()
                    if img is not None:
                        frames.append(img)
                        if self._resolution is None:
                            self._resolution = (frame.width, frame.height)
        except Exception as exc:
            log.debug("Decode error (non-fatal): %s", exc)
            self._codec = None
        return frames

    def reset(self) -> None:
        self._codec = None
        self._resolution = None

    @property
    def resolution(self) -> tuple[int, int] | None:
        return self._resolution

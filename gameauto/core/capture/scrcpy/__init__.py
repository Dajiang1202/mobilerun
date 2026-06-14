"""Scrcpy capture — low-latency screen capture via HOScrcpy SDK.

Usage::

    from gameauto.core.capture.scrcpy import ScrcpyCapture

    capture = ScrcpyCapture("serial", "sdk.jar")
    await capture.connect()
    png = await capture.screenshot()  # ~17ms

Self-contained.  Depends only on: jpype, av, cv2, numpy.
The SDK JAR must be downloaded separately (not bundled).
"""

from gameauto.core.capture.scrcpy.config import ScrcpyConfig
from gameauto.core.capture.scrcpy.capture import ScrcpyCapture

__all__ = ["ScrcpyConfig", "ScrcpyCapture"]

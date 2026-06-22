"""ohscrcpy — 鸿蒙 (HarmonyOS) 设备的低延迟截屏与触控。

基于 HOScrcpy SDK，通过 gRPC H.264 视频流把截图延迟从 ~500ms 降到 ~17ms。

快速开始::

    from ohscrcpy import Device

    dev = Device(serial="YOUR_SN", sdk_jar="hosScrcpy-1.0.15-beta.jar")
    dev.connect()
    dev.click(500, 1000)
    dev.save_screenshot("shot.png")
    dev.close()
"""

from ohscrcpy.bridge import (
    init,
    shutdown,
    screenshot,
    screenshot_bgr,
    resolution,
    output_resolution,
    touch,
    swipe,
)
from ohscrcpy.device import Device
from ohscrcpy.exceptions import (
    OhscrcpyError,
    OhscrcpyConnectionError,
    OhscrcpyStreamError,
    OhscrcpyTimeoutError,
)

__version__ = "0.1.0"

__all__ = [
    "Device",
    "init",
    "shutdown",
    "screenshot",
    "screenshot_bgr",
    "resolution",
    "output_resolution",
    "touch",
    "swipe",
    "OhscrcpyError",
    "OhscrcpyConnectionError",
    "OhscrcpyStreamError",
    "OhscrcpyTimeoutError",
]

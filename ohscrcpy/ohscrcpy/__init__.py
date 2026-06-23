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

# 默认路径(用户自备大附件)
_DEFAULT_SDK_JAR = "D:/resource/hosScrcpy-1.0.15-beta.jar"
_DEFAULT_JAVA_HOME = "D:/resource/jbr"


def connect(
    serial: str,
    scale: int = 2,
    max_fps: int = 15,
    sdk_jar: str = _DEFAULT_SDK_JAR,
    java_home: str = _DEFAULT_JAVA_HOME,
    jitter: int = 3,
) -> "Device":
    """连接设备, 返回已连接的 Device(一步到位)。

    用法::

        import ohscrcpy
        dev = ohscrcpy.connect("SN", scale=2, max_fps=15)
        png = dev.screenshot()     # 效率优先: 直接读缓存 ~0.5ms
        dev.tap(500, 1000)         # 原生像素坐标
        dev.swipe(100, 200, 300, 400, duration_ms=500)
        dev.close()

    Args:
        serial:    设备序列号。
        scale:     截图缩放(2=半尺寸减滞后; 1=原尺寸)。
        max_fps:   视频流帧率上限。
        sdk_jar:   HOScrcpy SDK JAR(默认 D:/resource/hosScrcpy-1.0.15-beta.jar)。
        java_home: JRE/JDK(默认 D:/resource/jbr)。
        jitter:    点击坐标随机抖动像素(0=关闭)。
    """
    dev = Device(serial, sdk_jar, java_home, scale=scale, max_fps=max_fps, jitter=jitter)
    dev.connect()
    return dev


__version__ = "0.1.0"

__all__ = [
    "Device",
    "connect",
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

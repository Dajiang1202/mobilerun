"""ohscrcpy 异常层次。"""


class OhscrcpyError(Exception):
    """所有 ohscrcpy 错误的基类。"""


class OhscrcpyConnectionError(OhscrcpyError):
    """设备未找到 / JVM 启动失败 / 视频流未就绪。"""


class OhscrcpyStreamError(OhscrcpyError):
    """视频流启动失败或中途断开。"""


class OhscrcpyTimeoutError(OhscrcpyError):
    """操作超时。"""

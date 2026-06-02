"""画面捕获抽象接口 — 所有截屏输入源的基类。

框架不关心画面从哪来：HDC 截屏、scrcpy 推流、USB 采集卡、甚至本地图片文件，
只需要实现 screenshot() → bytes 这一个方法。

扩展方式:
    继承 BaseCapture，实现 screenshot() / connect() / native_resolution。
    参考 HdcCapture 实现。
"""

from abc import ABC, abstractmethod


class BaseCapture(ABC):
    """画面捕获抽象 — 所有输入源必须实现这三个方法。

    screenshot() 返回原始 PNG/JPEG 字节流，不做任何缩放或处理。
    分辨率信息通过 native_resolution 属性暴露，供坐标转换使用。
    """

    @abstractmethod
    async def screenshot(self) -> bytes:
        """截取当前屏幕，返回 PNG 或 JPEG 字节流。

        每次调用都是一次完整的截屏操作，不做缓存。
        """
        ...

    @abstractmethod
    async def connect(self) -> None:
        """建立连接（如 HDC 握手、scrcpy 启动推流）。

        连接失败应抛出 ConnectionError，不要静默吞掉。
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """释放连接资源。"""
        ...

    @property
    @abstractmethod
    def native_resolution(self) -> tuple[int, int]:
        """返回截图的原始分辨率 (width, height)。

        这是坐标转换的基准。所有归一化坐标 [0-1000] 最终都要映射到这个分辨率。
        """
        ...

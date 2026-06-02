"""输入模拟抽象接口 — 所有设备操作的基类。

框架不关心操作怎么发出去：HDC 触摸命令、ADB input、scrcpy 反向控制，
只需要实现 swipe() 和 tap()。

坐标约定:
    所有坐标使用归一化 [0-1000] 空间，内部自动转换为设备像素坐标。
    这保证了同一个游戏 skill 在不同分辨率的设备上都能正确操作。
"""

from abc import ABC, abstractmethod


class BaseInput(ABC):
    """输入模拟抽象 — 所有设备操作必须实现。

    swipe() 和 tap() 接收归一化 [0-1000] 坐标，
    内部通过 input_resolution 映射到设备像素。
    """

    @abstractmethod
    async def swipe(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int = 1000) -> None:
        """从 (x1,y1) 滑动到 (x2,y2)。坐标均为归一化 [0-1000]。

        duration_ms 控制滑动速度（毫秒）。消消乐通常用 1000ms。
        """
        ...

    @abstractmethod
    async def tap(self, x: float, y: float, duration_ms: int = 100) -> None:
        """点击 (x,y)。坐标归一化 [0-1000]。"""
        ...

    @abstractmethod
    async def connect(self) -> None:
        """建立设备连接。"""
        ...

    @property
    @abstractmethod
    def input_resolution(self) -> tuple[int, int]:
        """返回输入坐标空间的分辨率 (width, height)。

        大多数情况下等于截图分辨率。iOS 需要重写（XCTest 逻辑点 vs 物理像素）。
        """
        ...

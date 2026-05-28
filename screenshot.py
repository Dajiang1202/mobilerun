"""截取当前设备屏幕并保存为 screenshot.png"""
import asyncio
from pathlib import Path

from mobilerun.config_manager.loader import ConfigLoader
from mobilerun.tools.driver import create_driver


async def main():
    config = ConfigLoader.load()
    serial = config.device.serial

    driver = create_driver(config.device)
    await driver.connect()
    print(f"已连接设备: {serial}")

    img_bytes = await driver.screenshot()

    output = Path("screenshot.png")
    output.write_bytes(img_bytes)
    print(f"截图已保存: {output.resolve()} ({(len(img_bytes) / 1024):.1f} KB)")


if __name__ == "__main__":
    asyncio.run(main())

"""
Quick Start: 打开相机 → 切换到录像模式 → 点击录像 → 等待3s → 停止录像
"""
import asyncio
import logging

from mobilerun import MobileAgent
from mobilerun.config_manager.loader import ConfigLoader


async def main():
    # 加载配置（自动读取 config.yaml，包含 API key、设备序列号等）
    config = ConfigLoader.load()

    # 任务描述
    goal = "点击退出按钮"

    # 创建 Agent（fastagent 模式，reasoning=false）
    agent = MobileAgent(goal=goal, config=config, timeout=300)

    # 运行并等待完成
    handler = agent.run()
    async for event in handler.stream_events():
        # 打印执行过程中的关键事件
        event_type = type(event).__name__
        if hasattr(event, "message") and event.message:
            print(f"[{event_type}] {event.message}")
        elif hasattr(event, "summary") and event.summary:
            print(f"[{event_type}] {event.summary}")

    result = await handler
    if result.success:
        print("\n✅ 任务完成！")
    else:
        print(f"\n❌ 任务失败: {result.message}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())

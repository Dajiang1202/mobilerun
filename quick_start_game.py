"""
Quick Start: 开心消消乐 — 执行一步棋（找到并执行一个有效的三消交换）
"""
import asyncio
import logging

from mobilerun import MobileAgent
from mobilerun.config_manager.loader import ConfigLoader


async def main():
    config = ConfigLoader.load()

    # 启用游戏模式
    config.agent.game_mode = True
    config.agent.max_steps = 5

    goal = (
        "在当前开心消消乐游戏棋盘上，找到一对相邻的棋子，交换后可以形成三消（3个或更多相同棋子连成一行或一列）。"
        "使用贪心算法从上到下、从左到右扫描棋盘，只执行一步有效的交换，然后完成任务。"
    )

    agent = MobileAgent(goal=goal, config=config, timeout=300)

    handler = agent.run()
    async for event in handler.stream_events():
        event_type = type(event).__name__
        if hasattr(event, "thought") and event.thought:
            print(f"\n{'='*60}")
            print(f"[思考] {event.thought}")
        if hasattr(event, "code") and event.code:
            print(f"[动作]\n{event.code}")
        if hasattr(event, "output") and event.output:
            print(f"[结果] {event.output}")

    result = await handler
    if result.success:
        print(f"\n✅ 任务完成！{result.reason}")
    else:
        print(f"\n❌ 任务失败: {result.reason}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())

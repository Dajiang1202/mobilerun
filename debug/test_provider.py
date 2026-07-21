"""HarmonyStateProvider 单元测试 — 验证双通道 state 输出

测试:
1. provider 能从 HarmonyOSDriver 拿到 state
2. UIState.elements 有内容、index 编号正确
3. formatted_text 包含截图说明 + UI 树
4. 元素筛选正确(跳过空容器)
"""
import asyncio
import os
from pathlib import Path

os.environ["PATH"] = r"d:\gameauto\mobilerun\tools\hdc;" + os.environ["PATH"]

from mobilerun.tools.driver.harmonyos import HarmonyOSDriver
from mobilerun.tools.ui.harmony_provider import HarmonyStateProvider


async def main():
    print("=" * 60)
    print("构造 driver + provider")
    print("=" * 60)
    driver = HarmonyOSDriver(
        hdc_path=r"d:\gameauto\mobilerun\tools\hdc\hdc.exe",
    )
    provider = HarmonyStateProvider(driver, use_normalized=True, max_elements=30)

    print("=" * 60)
    print("get_state()")
    print("=" * 60)
    state = await provider.get_state()

    print(f"✅ get_state 成功")
    print(f"   elements 数: {len(state.elements)}")
    print(f"   screen: {state.screen_width}x{state.screen_height}")
    print(f"   use_normalized: {state.use_normalized}")
    print(f"   phone_state: {state.phone_state}")

    print()
    print("=" * 60)
    print("formatted_text (前 2000 字符)")
    print("=" * 60)
    print(state.formatted_text[:2000])
    if len(state.formatted_text) > 2000:
        print(f"... (总长 {len(state.formatted_text)} 字符)")

    print()
    print("=" * 60)
    print("前 10 个元素(验证 index 编号)")
    print("=" * 60)
    for el in state.elements[:10]:
        print(f"  index={el['index']} type={el['type']} text={el.get('text','')!r} "
              f"id={el.get('id','')!r} bounds={el.get('bounds','')!r}")

    # 保存完整 formatted_text 供分析
    Path("debug/state_formatted_text.txt").write_text(
        state.formatted_text, encoding="utf-8"
    )
    print(f"\n完整 formatted_text 已保存到 debug/state_formatted_text.txt")


if __name__ == "__main__":
    asyncio.run(main())

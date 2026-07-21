"""端到端 driver 测试 — 验证改造后的 HarmonyOSDriver

测试:
1. 通过 HarmonyOSDriver 连接设备
2. get_ui_tree() 返回 mobilerun 标准格式
3. screenshot() 返回 bytes
4. tap/swipe 能跑(点屏幕中心)
"""
import asyncio
import os
import sys
from pathlib import Path

# hdc 加 PATH
os.environ["PATH"] = r"d:\gameauto\mobilerun\tools\hdc;" + os.environ["PATH"]

from mobilerun.tools.driver.harmonyos import HarmonyOSDriver


async def main():
    print("=" * 60)
    print("测试 1: 构造 + connect()")
    print("=" * 60)
    driver = HarmonyOSDriver(
        serial=None,  # 自动选设备
        hdc_path=r"d:\gameauto\mobilerun\tools\hdc\hdc.exe",
    )
    try:
        await driver.connect()
        print(f"✅ connect 成功")
        print(f"   platform = {driver.platform}")
        print(f"   supported = {sorted(driver.supported)}")
        print(f"   screen = {driver._screen_width}x{driver._screen_height}")
    except Exception as e:
        print(f"❌ connect 失败: {e}")
        import traceback
        traceback.print_exc()
        return

    print()
    print("=" * 60)
    print("测试 2: get_ui_tree()")
    print("=" * 60)
    try:
        state = await driver.get_ui_tree()
        tree = state.get("a11y_tree", [])
        print(f"✅ get_ui_tree 成功")
        print(f"   元素数: {len(tree)}")
        print(f"   phone_state: {state.get('phone_state')}")
        print(f"   screen_bounds: {state.get('device_context', {}).get('screen_bounds')}")
        # 显示前 8 个元素
        print(f"   前 8 个元素:")
        for el in tree[:8]:
            print(f"     [{el['index']}] type={el['type']} text={el.get('text','')!r} "
                  f"id={el.get('id','')!r} bounds={el.get('bounds','')!r}")
    except Exception as e:
        print(f"❌ get_ui_tree 失败: {e}")
        import traceback
        traceback.print_exc()

    print()
    print("=" * 60)
    print("测试 3: screenshot()")
    print("=" * 60)
    try:
        data = await driver.screenshot()
        print(f"✅ screenshot 成功: {len(data)} bytes")
        # 保存看看
        Path("debug/driver_screenshot.jpg").write_bytes(data)
        print(f"   已保存到 debug/driver_screenshot.jpg")
    except Exception as e:
        print(f"❌ screenshot 失败: {e}")
        import traceback
        traceback.print_exc()

    print()
    print("=" * 60)
    print("测试 4: get_apps()")
    print("=" * 60)
    try:
        apps = await driver.get_apps(include_system=False)
        print(f"✅ get_apps 成功: {len(apps)} 个非系统应用")
        # 找微信
        wechat = [a for a in apps if "tencent" in a.get("package", "").lower() or "wechat" in a.get("package", "").lower()]
        if wechat:
            print(f"   找到微信: {wechat}")
        else:
            print(f"   未找到微信, 前5个应用: {apps[:5]}")
    except Exception as e:
        print(f"❌ get_apps 失败: {e}")
        import traceback
        traceback.print_exc()

    print()
    print("=" * 60)
    print("测试 5: press_button('home') 回桌面")
    print("=" * 60)
    try:
        await driver.press_button("home")
        print(f"✅ press_button('home') 成功")
        await asyncio.sleep(1.5)
    except Exception as e:
        print(f"❌ press_button 失败: {e}")
        import traceback
        traceback.print_exc()

    print()
    print("=" * 60)
    print("全部 driver 测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

"""hmdriver2 真机连通性测试

验证:
1. Driver 能连真机
2. dump_hierarchy() 返回 UI 树
3. 截图能拿到
4. 设备信息能读到
"""
import sys
import json
from pathlib import Path

# 把 hdc 加到 PATH(hmdriver2 内部调 hdc)
import os
os.environ["PATH"] = r"d:\gameauto\mobilerun\tools\hdc;" + os.environ["PATH"]

from hmdriver2.driver import Driver
from hmdriver2.hdc import list_devices


def main():
    print("=" * 60)
    print("Step 1: 列出设备")
    print("=" * 60)
    devices = list_devices()
    print(f"设备列表: {devices}")
    if not devices:
        print("❌ 没有设备")
        sys.exit(1)

    serial = devices[0]
    print(f"使用设备: {serial}")

    print()
    print("=" * 60)
    print("Step 2: 构造 Driver(会自动连设备 + push agent.so)")
    print("=" * 60)
    try:
        d = Driver(serial)
        print(f"✅ Driver 构造成功, serial={d.serial}")
    except Exception as e:
        print(f"❌ Driver 构造失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print()
    print("=" * 60)
    print("Step 3: 设备信息")
    print("=" * 60)
    try:
        info = d.device_info()
        print(f"✅ 设备信息: {info}")
    except Exception as e:
        print(f"⚠️ device_info 失败: {e}")

    try:
        w, h = d.display_size()
        print(f"✅ 屏幕尺寸: {w}x{h}")
    except Exception as e:
        print(f"⚠️ display_size 失败: {e}")

    print()
    print("=" * 60)
    print("Step 4: dump_hierarchy()")
    print("=" * 60)
    try:
        hierarchy = d.dump_hierarchy()
        # 统计节点数
        def count_nodes(node):
            n = 1
            for c in node.get("children", []):
                n += count_nodes(c)
            return n

        total = count_nodes(hierarchy)
        print(f"✅ dump_hierarchy 成功, 总节点数: {total}")

        # 打印前几个有 text 的节点
        def find_text_nodes(node, out, limit=10):
            if len(out) >= limit:
                return
            attrs = node.get("attributes", {})
            if attrs.get("text") or attrs.get("id") or attrs.get("description"):
                out.append(attrs)
            for c in node.get("children", []):
                find_text_nodes(c, out, limit)

        text_nodes = []
        find_text_nodes(hierarchy, text_nodes)
        print(f"   有标识(text/id/desc)的节点示例(前10个):")
        for i, attrs in enumerate(text_nodes[:10]):
            print(f"   [{i}] type={attrs.get('type','?')} text={attrs.get('text','')!r} "
                  f"id={attrs.get('id','')!r} bounds={attrs.get('bounds','')!r}")

        # 保存完整树到文件供分析
        out_file = Path("debug/hierarchy_sample.json")
        out_file.parent.mkdir(exist_ok=True)
        out_file.write_text(json.dumps(hierarchy, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   完整树已保存到: {out_file}")
    except Exception as e:
        print(f"❌ dump_hierarchy 失败: {e}")
        import traceback
        traceback.print_exc()

    print()
    print("=" * 60)
    print("Step 5: 截图")
    print("=" * 60)
    try:
        out_path = "debug/test_screenshot.png"
        Path(out_path).parent.mkdir(exist_ok=True)
        returned = d.screenshot(out_path)
        size = Path(out_path).stat().st_size if Path(out_path).exists() else 0
        print(f"✅ 截图成功: 返回={returned}, 文件大小={size} bytes")
    except Exception as e:
        print(f"❌ 截图失败: {e}")
        import traceback
        traceback.print_exc()

    print()
    print("=" * 60)
    print("Step 6: 当前 App")
    print("=" * 60)
    try:
        bundle, ability = d.current_app()
        print(f"✅ 当前 App: bundle={bundle}, ability={ability}")
    except Exception as e:
        print(f"⚠️ current_app 失败: {e}")

    print()
    print("=" * 60)
    print("全部测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()

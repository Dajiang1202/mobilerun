#!/usr/bin/env python3
"""ohscrcpy 快速开始 —— 鸿蒙设备截屏 + 触控一站式示例。

直接运行::

    python quickstart.py            # 跑全部示例
    python quickstart.py preview    # 只开实时预览

运行前先改下面的 ═► 配置区 ═，填入设备序列号和 SDK JAR 路径。
"""

from __future__ import annotations

import os
import sys
import time

# 让 `import ohscrcpy` 能找到同目录的包 (无论从哪运行)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ohscrcpy import Device

# ════════════════════════════════════════════════════════════════════
#  ► 配置区  ——  改这里就够，无需命令行传参
# ════════════════════════════════════════════════════════════════════

# 设备序列号。命令行 `hdc list targets` 查看。
DEVICE_SERIAL = "YOUR_DEVICE_SN"

# HOScrcpy SDK JAR 路径 (绝对或相对本文件)。
# 下载见 README.md。默认在当前目录和上层 gameauto/resource/ 找。
SDK_JAR = "hosScrcpy-1.0.15-beta.jar"

# JDK/JRE 路径 (留空自动探测 DevEco Studio 自带 JBR / JAVA_HOME 环境变量)。
JAVA_HOME = ""  # 例如: "E:/DevEco Studio/jbr"

# 截图输出缩放系数: 1=原分辨率, 2=二分之一, 3=三分之一 ...
SCALE = 2

# 视频流帧率上限 (1-60)，超出部分客户端跳帧丢弃。
MAX_FPS = 30

# ════════════════════════════════════════════════════════════════════


def _make_device() -> Device:
    """按配置创建并连接设备，返回 Device。"""
    # SDK JAR 路径解析: 显式路径 → 当前目录 → gameauto/resource 兜底
    jar = SDK_JAR
    if not os.path.isabs(jar) and not os.path.exists(jar):
        here = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(here, jar),
            os.path.join(here, "..", "gameauto", "resource", os.path.basename(jar)),
            os.path.join(here, "..", "gameauto", "resource", "hosScrcpy-1.0.15-beta.jar"),
        ]
        for c in candidates:
            if os.path.exists(c):
                jar = os.path.abspath(c)
                break
    if not os.path.exists(jar):
        print(f"❌ 找不到 SDK JAR: {jar}")
        print("   修改 quickstart.py 顶部的 SDK_JAR，或把 jar 放到当前目录。")
        sys.exit(1)

    dev = Device(
        serial=DEVICE_SERIAL,
        sdk_jar=jar,
        java_home=JAVA_HOME,
        scale=SCALE,
        max_fps=MAX_FPS,
    )
    dev.connect()
    return dev


def demo_screenshot(dev: Device) -> None:
    """① 截屏 —— 连续抓 5 帧，统计耗时，保存一帧。"""
    print("\n" + "=" * 60)
    print("① 截屏示例")
    print("=" * 60)
    w, h = dev.resolution
    print(f"原生分辨率: {w}x{h} | 输出: {dev.output_resolution[0]}x{dev.output_resolution[1]}")

    for i in range(5):
        t0 = time.perf_counter()
        png = dev.screenshot()
        ms = (time.perf_counter() - t0) * 1000
        print(f"  帧 {i + 1}: {len(png):,}B,  {ms:.1f}ms")

    out = dev.save_screenshot("ohscrcpy_shot.png")
    print(f"  已保存: {out}")


def demo_click(dev: Device) -> None:
    """② 单击 —— 点屏幕中心。"""
    print("\n" + "=" * 60)
    print("② 单击示例")
    print("=" * 60)
    w, h = dev.resolution
    cx, cy = w // 2, h // 2
    ms = dev.click(cx, cy, duration_ms=50)
    print(f"  点击中心 ({cx},{cy}): {ms:.2f}ms")


def demo_multi_click(dev: Device) -> None:
    """③ 连点 —— 同一位置快速点 5 次 (模拟抽卡/连抽按钮)。"""
    print("\n" + "=" * 60)
    print("③ 连点示例 (5 次, 间隔 0.15s)")
    print("=" * 60)
    w, h = dev.resolution
    cx, cy = w // 2, h // 2
    t0 = time.perf_counter()
    dev.multi_click(cx, cy, times=5, interval=0.15, duration_ms=50)
    print(f"  连点完成, 总耗时 {(time.perf_counter() - t0) * 1000:.0f}ms")


def demo_swipe(dev: Device) -> None:
    """④ 滑动 —— 从屏幕下 3/4 滑到上 1/4 (上滑)。"""
    print("\n" + "=" * 60)
    print("④ 滑动示例 (上滑)")
    print("=" * 60)
    w, h = dev.resolution
    x = w // 2
    ms = dev.swipe(x, int(h * 0.75), x, int(h * 0.25), duration_ms=400)
    print(f"  上滑完成: {ms:.0f}ms")


def demo_preview(dev: Device) -> None:
    """⑤ 实时预览 —— 按 q 退出, 按 s 截图。"""
    import cv2

    print("\n" + "=" * 60)
    print("⑤ 实时预览 (按 q 退出, 按 s 截图)")
    print("=" * 60)
    ow, oh = dev.output_resolution
    cv2.namedWindow("ohscrcpy", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("ohscrcpy", max(1, ow), max(1, oh))

    save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captured")
    os.makedirs(save_dir, exist_ok=True)
    save_count = 0
    fps_last, fps_count, fps_cur = time.perf_counter(), 0, 0.0

    while True:
        frame = dev.screenshot_bgr()
        if frame is None:
            time.sleep(0.01)
            continue

        fps_count += 1
        now = time.perf_counter()
        if now - fps_last >= 1.0:
            fps_cur = fps_count / (now - fps_last)
            fps_count, fps_last = 0, now

        fh, fw = frame.shape[:2]
        cv2.setWindowTitle("ohscrcpy", f"ohscrcpy — {fw}x{fh} @ {fps_cur:.0f}FPS | q 退出 s 截图")
        cv2.imshow("ohscrcpy", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        elif key == ord("s"):
            save_count += 1
            fname = os.path.join(save_dir, f"shot_{save_count:04d}.png")
            cv2.imwrite(fname, frame)
            print(f"  已保存: {fname}")

    cv2.destroyAllWindows()


def main() -> None:
    if DEVICE_SERIAL == "YOUR_DEVICE_SN":
        print("❌ 请先在 quickstart.py 顶部把 DEVICE_SERIAL 改成真实设备序列号")
        print("   查看序列号: hdc list targets")
        sys.exit(1)

    only_preview = len(sys.argv) > 1 and sys.argv[1] == "preview"

    dev = _make_device()
    try:
        if only_preview:
            demo_preview(dev)
            return
        demo_screenshot(dev)
        demo_click(dev)
        demo_multi_click(dev)
        demo_swipe(dev)
        print("\n✅ 全部示例完成。想看实时预览: python quickstart.py preview")
    finally:
        dev.close()


if __name__ == "__main__":
    main()

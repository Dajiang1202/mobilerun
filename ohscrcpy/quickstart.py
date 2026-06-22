#!/usr/bin/env python3
"""ohscrcpy 快速开始 —— 鸿蒙设备截屏 + 触控一站式示例。

直接运行::

    python quickstart.py

运行前先改 ``main()`` 里的 ═► 配置区 ═，填入设备序列号等参数。
所有参数都是 main() 的局部变量，直接传给 Device，不用命令行、不读全局。
"""

from __future__ import annotations

import os
import sys
import time

# 让 `import ohscrcpy` 能找到同目录的包 (无论从哪运行)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ohscrcpy import Device


DEFAULT_SDK_JAR = "hosScrcpy-1.0.15-beta.jar"


def resolve_sdk_jar(jar: str) -> str:
    """解析 SDK JAR: 绝对/显式路径 → 当前目录 → gameauto/resource 兜底。"""
    if os.path.isabs(jar) and os.path.exists(jar):
        return jar
    if os.path.exists(jar):
        return os.path.abspath(jar)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, jar),
        os.path.join(here, "..", "gameauto", "resource", os.path.basename(jar)),
        os.path.join(here, "..", "gameauto", "resource", DEFAULT_SDK_JAR),
    ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    return jar  # 返回原名, connect 时报错给提示


def make_device(sn: str, sdk_jar: str, java_home: str = "",
                scale: int = 2, max_fps: int = 30, jitter: int = 3) -> Device:
    """按参数创建并连接 Device。所有参数显式传入, 不读任何全局。"""
    sdk_jar = resolve_sdk_jar(sdk_jar)
    if not os.path.exists(sdk_jar):
        print(f"[ERROR] 找不到 SDK JAR: {sdk_jar}")
        print("        改 main() 里的 sdk_jar, 或把 jar 放到当前目录。")
        sys.exit(1)

    dev = Device(
        serial=sn,
        sdk_jar=sdk_jar,
        java_home=java_home,
        scale=scale,
        max_fps=max_fps,
        jitter=jitter,
    )
    dev.connect()
    return dev


# ════════════════════════════════════════════════════════════════════
#  示例 —— 每个都接收已连接的 Device, 不读任何全局状态
# ════════════════════════════════════════════════════════════════════

def demo_screenshot(dev: Device) -> None:
    """① 截屏 —— 连续抓 5 帧, 统计耗时, 保存一帧。"""
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
    """③ 连点 —— 同一位置快速点 5 次。"""
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


# ════════════════════════════════════════════════════════════════════

def main() -> None:
    # ══════════════════════════════════════════════════════════════
    #  ► 配置区  ——  直接改这些局部变量
    # ══════════════════════════════════════════════════════════════

    # 设备序列号 (hdc list targets 查看)
    sn = "YOUR_DEVICE_SN"

    # HOScrcpy SDK JAR 路径 (绝对或相对本文件)
    sdk_jar = DEFAULT_SDK_JAR

    # JDK/JRE 路径 (留空自动探测 DevEco JBR / JAVA_HOME)
    java_home = ""  # 例如 "E:/DevEco Studio/jbr"

    # 截图输出缩放系数: 1=原分辨率, 2=二分之一, 3=三分之一
    scale = 2

    # 视频流帧率上限 (1-60)
    max_fps = 30

    # 点击坐标随机抖动像素数, 0 关闭
    jitter = 3

    # 跑哪些示例: "all" 或 "screenshot" / "click" / "multi_click" / "swipe" / "preview"
    demo = "all"

    # ══════════════════════════════════════════════════════════════

    if sn == "YOUR_DEVICE_SN":
        print("[ERROR] 请先把 main() 里的 sn 改成真实设备序列号 (hdc list targets 查看)")
        sys.exit(1)

    dev = make_device(
        sn=sn,
        sdk_jar=sdk_jar,
        java_home=java_home,
        scale=scale,
        max_fps=max_fps,
        jitter=jitter,
    )

    try:
        if demo == "all":
            demo_screenshot(dev)
            demo_click(dev)
            demo_multi_click(dev)
            demo_swipe(dev)
            print("\n[OK] 全部示例完成。想看实时预览: 把 demo 改成 'preview'")
        elif demo == "preview":
            demo_preview(dev)
        else:
            {
                "screenshot": demo_screenshot,
                "click": demo_click,
                "multi_click": demo_multi_click,
                "swipe": demo_swipe,
            }[demo](dev)
    finally:
        dev.close()


if __name__ == "__main__":
    main()

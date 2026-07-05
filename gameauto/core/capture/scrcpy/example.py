#!/usr/bin/env python3
"""Scrcpy 模块快速调用示例。

直接运行:  python example.py
按 'q' 退出预览, 's' 保存截图。

══════════════════════════════════════════════════════════════════════
  可配置参数 (修改下面变量即可，无需命令行传参)
══════════════════════════════════════════════════════════════════════
"""

import os
import sys

# ═══════════════════════════════════════════════════════════════════
#  设备配置
# ═══════════════════════════════════════════════════════════════════

# 设备序列号 (hdc list targets 查看)
DEVICE_SERIAL = "4NZ0225613000015"

# SDK JAR 路径 — 基于 __file__ 定位, 换电脑/换工作目录都能找到
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))
SDK_JAR = os.path.join(_PROJECT_ROOT, "gameauto", "resource", "hosScrcpy-1.0.15-beta.jar")

# JDK/JRE 路径 (留空则自动检测 DevEco Studio 自带 JBR)
JAVA_HOME = ""  # 例如: "E:/DevEco Studio/jbr"

# ═══════════════════════════════════════════════════════════════════
#  视频流配置
# ═══════════════════════════════════════════════════════════════════

# 缩放比例（整数，基于真机原生分辨率等比缩放）:
#   1 — 原生分辨率（如 1276×2848）
#   2 — 二分之一（如 638×1424）
#   3 — 三分之一（如 425×949）
#   4 — 四分之一（如 319×712）
#   原生分辨率在 init 时从设备自动读取
SCALE = 2

# 目标帧率 (1-60，通过跳帧实现，不影响带宽)
MAX_FPS = 5

# ═══════════════════════════════════════════════════════════════════
#  预览窗口配置 (仅 example.py 使用，不影响 bridge API)
# ═══════════════════════════════════════════════════════════════════

# 预览窗口缩放比例 (0.5 = 缩小一半, 1.0 = 原始输出分辨率)
PREVIEW_SCALE = 1.0

# ═══════════════════════════════════════════════════════════════════

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import cv2
import time

from gameauto.core.capture.scrcpy.bridge import (
    init, shutdown, screenshot, screenshot_bgr, resolution, touch, swipe,
)


def example_1_basic_capture():
    """示例 1: 基础截图 — 获取最新帧并保存为 PNG"""
    print("=" * 60)
    print("示例 1: 基础截图")
    print("=" * 60)

    init(DEVICE_SERIAL, SDK_JAR, JAVA_HOME, scale=SCALE, max_fps=MAX_FPS)
    w, h = resolution()
    print(f"原生分辨率: {w}x{h}")

    for i in range(5):
        t0 = time.perf_counter()
        png = screenshot()
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"  截图 {i+1}: {len(png):,}B, 耗时 {elapsed:.1f}ms")

    shutdown()


def example_2_touch():
    """示例 2: 触控 — 高精度点击和滑动"""
    print("\n" + "=" * 60)
    print("示例 2: 触控")
    print("=" * 60)

    init(DEVICE_SERIAL, SDK_JAR, JAVA_HOME, scale=SCALE, max_fps=MAX_FPS)
    w, h = resolution()

    # 点击屏幕中央
    t0 = time.perf_counter()
    touch(w // 2, h // 2, duration_ms=100)
    print(f"  点击中心({w//2},{h//2}): {(time.perf_counter() - t0) * 1000:.2f}ms")
    time.sleep(2)
    # 上滑
    t0 = time.perf_counter()
    swipe(w // 2, h * 3 // 4, w // 2, h // 4, duration_ms=500)
    print(f"  上滑: {(time.perf_counter() - t0) * 1000:.0f}ms")

    shutdown()


def example_3_preview():
    """示例 3: 实时预览 — 类似 cv2.imshow"""
    print("\n" + "=" * 60)
    print("示例 3: 实时预览 (按 'q' 退出, 按 's' 截图)")
    print("=" * 60)

    init(DEVICE_SERIAL, SDK_JAR, JAVA_HOME, scale=SCALE, max_fps=MAX_FPS)
    w, h = resolution()
    print(f"原生分辨率: {w}x{h}")
    ow, oh = w // SCALE, h // SCALE
    print(f"输出分辨率: {ow}x{oh} (scale={SCALE})")

    cv2.namedWindow("Scrcpy Preview", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Scrcpy Preview",
                     max(1, int(ow * PREVIEW_SCALE)),
                     max(1, int(oh * PREVIEW_SCALE)))

    save_dir = os.path.join(os.path.dirname(__file__), "captured")
    os.makedirs(save_dir, exist_ok=True)
    save_count = 0
    fps_interval = 1.0
    fps_last = time.perf_counter()
    fps_count = 0
    fps_current = 0.0
    # 跟踪当前窗口尺寸，横竖屏切换时自动调整
    current_w, current_h = ow, oh

    print(f"截图保存目录: {save_dir}")

    while True:
        frame = screenshot_bgr()
        if frame is None:
            time.sleep(0.01)
            continue

        # FPS 统计
        fps_count += 1
        now = time.perf_counter()
        if now - fps_last >= fps_interval:
            fps_current = fps_count / (now - fps_last)
            fps_count = 0
            fps_last = now

        fh, fw = frame.shape[:2]

        # 横竖屏切换时自动调整预览窗口
        if fw != current_w or fh != current_h:
            current_w, current_h = fw, fh
            cv2.resizeWindow("Scrcpy Preview",
                             max(1, int(fw * PREVIEW_SCALE)),
                             max(1, int(fh * PREVIEW_SCALE)))
            print(f"  分辨率变更: 输出 {fw}x{fh}  (原生 {resolution()[0]}x{resolution()[1]})")

        cv2.setWindowTitle(
            "Scrcpy Preview",
            f"Scrcpy Preview — {fw}x{fh} @ {fps_current:.0f} FPS | 按 q 退出 按 s 截图"
        )
        cv2.imshow("Scrcpy Preview", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('s'):
            save_count += 1
            fname = os.path.join(save_dir, f"shot_{save_count:04d}.png")
            cv2.imwrite(fname, frame)
            print(f"  已保存: {fname}")

    cv2.destroyAllWindows()
    shutdown()


def example_4_asyncio():
    """示例 4: 异步集成 — 对接 GameAuto 框架的 BaseCapture 接口"""
    import asyncio

    async def main():
        from gameauto.core.capture.scrcpy.capture import ScrcpyCapture

        capture = ScrcpyCapture(
            DEVICE_SERIAL, SDK_JAR, JAVA_HOME,
            scale=SCALE, max_fps=MAX_FPS,
        )
        await capture.connect()

        for i in range(3):
            t0 = time.perf_counter()
            png = await capture.screenshot()
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"  截图 {i+1}: {len(png):,}B, 耗时 {elapsed:.1f}ms")

        await capture.disconnect()

    print("\n" + "=" * 60)
    print("示例 4: 异步集成 (BaseCapture 接口)")
    print("=" * 60)
    asyncio.run(main())


# ═══════════════════════════════════════════════════════════════════
#  选择运行哪个示例 (修改这个数字)
#    1 = 基础截图
#    2 = 触控测试
#    3 = 实时预览
#    4 = 异步集成 (BaseCapture 接口)
# ═══════════════════════════════════════════════════════════════════
RUN_DEMO = 2

# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if RUN_DEMO == 1:
        example_1_basic_capture()
    elif RUN_DEMO == 2:
        example_2_touch()
    elif RUN_DEMO == 3:
        example_3_preview()
    elif RUN_DEMO == 4:
        example_4_asyncio()
    else:
        print(f"未知示例: {RUN_DEMO}，可选 [1,2,3,4]")

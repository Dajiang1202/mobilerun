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

# SDK JAR 路径 (相对于 gameauto 根目录)
SDK_JAR = "gameauto/resource/hosScrcpy-1.0.15-beta.jar"

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
MAX_FPS = 10

# 视频码率 (bps，0=SDK默认~8Mbps，设值则覆盖)
#   功耗/带宽参考:
#     8_000_000 (8Mbps)  — SDK 默认，功耗最高
#     4_000_000 (4Mbps)  — 标准码率，scale=2 够用
#     2_000_000 (2Mbps)  — 低码率，功耗明显下降，画质可接受
#     1_000_000 (1Mbps)  — 极低码率，有画质损失但截图识别仍可用
#     500_000   (0.5Mbps)— 最低可用，仅适合静态画面
BITRATE = 0

# I帧间隔 (秒，0=SDK默认，设值则覆盖)
#   越大压缩率越高越省带宽，但首帧/场景切换延迟增加
#   建议: 5 (每5秒一个关键帧，备战阶段够用)
I_FRAME_INTERVAL = 0

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

    init(DEVICE_SERIAL, SDK_JAR, JAVA_HOME, scale=SCALE, max_fps=MAX_FPS,
         bitrate=BITRATE, i_frame_interval=I_FRAME_INTERVAL)
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

    init(DEVICE_SERIAL, SDK_JAR, JAVA_HOME, scale=SCALE, max_fps=MAX_FPS,
         bitrate=BITRATE, i_frame_interval=I_FRAME_INTERVAL)
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

    init(DEVICE_SERIAL, SDK_JAR, JAVA_HOME, scale=SCALE, max_fps=MAX_FPS,
         bitrate=BITRATE, i_frame_interval=I_FRAME_INTERVAL)
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
            bitrate=BITRATE, i_frame_interval=I_FRAME_INTERVAL,
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


def example_5_low_bitrate():
    """示例 5: 低码率对比 — 测试不同码率下的画质与功耗"""
    import asyncio

    async def test_bitrate(bitrate: int, label: str):
        print(f"\n--- {label}: bitrate={bitrate/1_000_000:.1f}Mbps ---")
        from gameauto.core.capture.scrcpy.capture import ScrcpyCapture

        capture = ScrcpyCapture(
            DEVICE_SERIAL, SDK_JAR, JAVA_HOME,
            scale=SCALE, max_fps=MAX_FPS,
            bitrate=bitrate, i_frame_interval=I_FRAME_INTERVAL if I_FRAME_INTERVAL else 5,
        )
        await capture.connect()

        # 截图几张看画质和大小
        for i in range(3):
            t0 = time.perf_counter()
            png = await capture.screenshot()
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"  截图 {i+1}: {len(png):,}B, 耗时 {elapsed:.1f}ms")
            # 保存对比图
            save_dir = os.path.join(os.path.dirname(__file__), "captured")
            os.makedirs(save_dir, exist_ok=True)
            fname = os.path.join(save_dir, f"bitrate_{bitrate//1_000_000}M_{i+1}.png")
            with open(fname, "wb") as f:
                f.write(png)

        await capture.disconnect()
        print(f"  截图已保存到 captured/bitrate_{bitrate//1_000_000}M_*.png")

    async def main():
        # 测试三种码率: 8M(默认), 2M(低), 0.5M(极低)
        test_bitrates = [
            (8_000_000, "默认高码率"),
            (2_000_000, "低码率(推荐)"),
            (500_000,   "极低码率"),
        ]
        for br, label in test_bitrates:
            await test_bitrate(br, label)

        print("\n对比 captured/ 目录下三组截图，观察画质差异")

    print("\n" + "=" * 60)
    print("示例 5: 低码率画质对比")
    print("=" * 60)
    print("依次用 8M / 2M / 0.5M 码率截图，保存到 captured/ 对比")
    print("观察: 文件大小变化、文字/图标清晰度")
    asyncio.run(main())


# ═══════════════════════════════════════════════════════════════════
#  选择运行哪个示例 (修改这个数字)
#    1 = 基础截图
#    2 = 触控测试
#    3 = 实时预览
#    4 = 异步集成 (BaseCapture 接口)
#    5 = 低码率画质对比 ⭐
# ═══════════════════════════════════════════════════════════════════
RUN_DEMO = 3

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
    elif RUN_DEMO == 5:
        example_5_low_bitrate()
    else:
        print(f"未知示例: {RUN_DEMO}，可选 [1,2,3,4,5]")

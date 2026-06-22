"""Low-level JPype bridge to the HOScrcpy SDK.

Internal module — use ``ohscrcpy.Device`` for the public API.

坐标模型:
    - 原生分辨率: 从设备自动探测 (如 1276×2848)
    - 输出分辨率: native / scale (如 scale=2 → 638×1424)
    - touch / swipe 始终使用**原生**像素坐标
    - screenshot 返回输出分辨率 (缩放后) 的帧
"""

from __future__ import annotations

import logging
import os
import threading
import time

import numpy as np

log = logging.getLogger("ohscrcpy.bridge")

# ── 状态 ───────────────────────────────────────────────────────────────

_jvm_started = False
_device = None
_codec = None
_cb_proxy = None

_latest_png: bytes | None = None
_latest_bgr: np.ndarray | None = None
_frame_lock = threading.Lock()
_frame_count = 0

_native_w = 0
_native_h = 0
_output_w = 0
_output_h = 0

_stream_ready = threading.Event()

_scale = 2
_max_fps = 30


# ── 公开 API ──────────────────────────────────────────────────────────

def init(
    serial: str,
    sdk_jar: str,
    java_home: str = "",
    scale: int = 2,
    max_fps: int = 30,
) -> None:
    """启动 JVM、连接设备、开启视频流。幂等（重复调用直接返回）。

    Args:
        serial:    设备序列号 (hdc list targets 查看)。
        sdk_jar:   HOScrcpy SDK JAR 路径。
        java_home: JDK/JRE 路径，留空自动探测。
        scale:     缩放系数，1=原分辨率，2=二分之一，3=三分之一 ...
        max_fps:   帧率上限 (1-60)，超出部分客户端跳帧丢弃。
    """
    global _jvm_started, _scale, _max_fps
    if _jvm_started:
        return
    _scale = max(1, scale)
    _max_fps = max_fps
    _start_jvm(sdk_jar, java_home)
    _start_device(serial)
    _start_stream()
    _jvm_started = True


def shutdown() -> None:
    """停止视频流并关闭 JVM。"""
    global _jvm_started, _device, _cb_proxy
    if not _jvm_started:
        return
    try:
        if _device:
            _device.stopCaptureScreen()
    except Exception:
        pass
    try:
        import jpype
        jpype.shutdownJVM()
    except Exception:
        pass
    _jvm_started = False
    _device = None
    _cb_proxy = None


def screenshot(wait_new: bool = False, timeout: float = 1.0) -> bytes:
    """返回最新帧的 PNG bytes (输出分辨率)。

    wait_new=True 时等待至少一帧新画面到达再返回 —— 视频流在 JVM 线程异步更新
    _latest_png，点击后立即读取会取到过期缓存帧。超时无新帧则告警。
    """
    if wait_new:
        start = _frame_count
        t0 = time.perf_counter()
        while _frame_count <= start and time.perf_counter() - t0 < timeout:
            time.sleep(0.01)
        if _frame_count <= start:
            log.warning("ohscrcpy: %.1fs 内无新帧, 视频流可能停滞", timeout)
    with _frame_lock:
        return _latest_png or b""


def screenshot_bgr() -> np.ndarray | None:
    """返回最新帧的 BGR numpy 数组 (输出分辨率)。"""
    with _frame_lock:
        return _latest_bgr.copy() if _latest_bgr is not None else None


def resolution() -> tuple[int, int]:
    """设备原生分辨率 (用于坐标映射)。"""
    return (_native_w, _native_h)


def output_resolution() -> tuple[int, int]:
    """缩放后的输出分辨率 (用于显示/截图)。"""
    return (_output_w, _output_h)


def touch(x: int, y: int, duration_ms: int = 50) -> float:
    """在原生像素坐标点击。返回耗时(ms)。"""
    t0 = time.perf_counter()
    _device.onTouchDown(x, y)
    if duration_ms > 0:
        time.sleep(duration_ms / 1000.0)
    _device.onTouchUp(x, y)
    return (time.perf_counter() - t0) * 1000


def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 500) -> float:
    """在原生像素坐标滑动。返回耗时(ms)。"""
    t0 = time.perf_counter()
    _device.onTouchDown(x1, y1)
    steps = max(5, duration_ms // 16)
    step_time = duration_ms / 1000.0 / steps
    for i in range(1, steps + 1):
        t = i / steps
        _device.onTouchMove(int(x1 + (x2 - x1) * t), int(y1 + (y2 - y1) * t))
        time.sleep(step_time)
    _device.onTouchUp(x2, y2)
    return (time.perf_counter() - t0) * 1000


def is_online() -> bool:
    return _device.isOnline()


# ── 内部实现 ──────────────────────────────────────────────────────────

def _start_jvm(sdk_jar: str, java_home: str):
    if java_home:
        os.environ["JAVA_HOME"] = java_home
    if not os.environ.get("JAVA_HOME"):
        # 搜索顺序: 本项目 tools/jbr → DevEco Studio → 系统常见路径
        for d in [
            "D:/resource/jbr",
            "E:/DevEco Studio/jbr",
            "C:/DevEco Studio/jbr",
            os.environ.get("JAVA_HOME", ""),
        ]:
            if d and os.path.isdir(d):
                os.environ["JAVA_HOME"] = d
                break

    import jpype
    import jpype.imports  # noqa: F401
    jh = os.environ.get("JAVA_HOME", "")
    jvm_path = None
    for dll in [f"{jh}/bin/server/jvm.dll", f"{jh}/jre/bin/server/jvm.dll"]:
        if os.path.exists(dll):
            jvm_path = dll
            break
    if not jvm_path:
        raise RuntimeError(
            f"JVM (jvm.dll) not found under JAVA_HOME={jh!r}. "
            "安装 DevEco Studio 或 OpenJDK，并设置 java_home 参数 / JAVA_HOME 环境变量。"
        )

    log.info("JVM: %s", jvm_path)
    jpype.startJVM(jvm_path, classpath=[sdk_jar], convertStrings=True)


def _start_device(serial: str):
    global _device
    from com.huawei.hosscrcpy.api import HosRemoteDevice, HosRemoteConfig
    _device = HosRemoteDevice(HosRemoteConfig(serial))
    log.info("Device: %s, online=%s", serial, _device.isOnline())


def _start_stream():
    global _codec, _cb_proxy
    global _native_w, _native_h, _output_w, _output_h
    import av
    import cv2

    _codec = av.CodecContext.create("h264", "r")
    _codec.options["threads"] = "auto"
    _codec.options["delay"] = "0"

    frame_interval = 1.0 / _max_fps if _max_fps > 0 else 0
    last_frame_time = 0.0

    def on_data(buf):
        nonlocal last_frame_time
        global _latest_png, _latest_bgr, _frame_count
        global _native_w, _native_h, _output_w, _output_h
        try:
            raw = bytes(buf.array()[:buf.remaining()])
            for packet in _codec.parse(raw):
                for frame in _codec.decode(packet):
                    img = frame.to_image()
                    if img is None:
                        continue

                    # 帧率限制
                    now = time.perf_counter()
                    if frame_interval > 0 and now - last_frame_time < frame_interval:
                        continue
                    last_frame_time = now

                    # 从首帧捕获原生分辨率
                    if not _native_w:
                        _native_w, _native_h = frame.width, frame.height
                        _output_w, _output_h = _native_w // _scale, _native_h // _scale
                        log.info("Native: %dx%d, output: %dx%d (scale=%d)",
                                 _native_w, _native_h, _output_w, _output_h, _scale)

                    # 转换 + 缩放
                    bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGBA2BGR)
                    tw, th = _output_w, _output_h
                    if _scale > 1 and (bgr.shape[1], bgr.shape[0]) != (tw, th):
                        bgr = cv2.resize(bgr, (tw, th), interpolation=cv2.INTER_LINEAR)

                    _, png = cv2.imencode(".png", bgr)

                    with _frame_lock:
                        _latest_bgr = bgr
                        _latest_png = png.tobytes()
                        _frame_count += 1
        except Exception:
            pass

    def on_exception(t):
        log.warning("Stream: %s", t.getMessage() if t.getMessage() else str(t))

    def on_ready():
        _stream_ready.set()

    import jpype
    _cb_proxy = jpype.JProxy(
        "com.huawei.hosscrcpy.api.ScreenCapCallback",
        {"onData": on_data, "onException": on_exception, "onReady": on_ready},
    )

    log.info("Video stream: scale=%d @ %dfps", _scale, _max_fps)
    _device.startCaptureScreen(_cb_proxy)

    if not _stream_ready.wait(timeout=30):
        log.warning("Stream onReady not fired after 30s")

    # 等待首帧解码 (SDK 内部可能重试 gRPC 连接)
    for attempt in range(120):  # 最多 60 秒
        with _frame_lock:
            if _frame_count > 0:
                break
        time.sleep(0.5)
        if attempt == 60:
            log.warning("Still waiting for first frame (SDK may be retrying)...")

    if _frame_count == 0:
        raise RuntimeError(
            "Video stream failed to deliver frames. Check device connection and retry."
        )

    log.info("Stream live: %d frames, native %dx%d",
             _frame_count, _native_w, _native_h)

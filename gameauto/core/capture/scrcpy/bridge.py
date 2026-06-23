"""Low-level JPype bridge to HOScrcpy SDK.

Internal module — use ScrcpyCapture for the public API.

Coordinate model:
    - Native resolution: auto-detected from device (e.g. 1276×2848)
    - Output resolution: native / scale (e.g. scale=2 → 638×1424)
    - touch/swipe always use native coordinates
    - screenshot returns frames at output resolution
"""

from __future__ import annotations

import logging
import os
import threading
import time

import numpy as np

log = logging.getLogger("gameauto.capture.scrcpy.bridge")

# ── State ─────────────────────────────────────────────────────────────────

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


# ── Public API ────────────────────────────────────────────────────────────

def init(
    serial: str,
    sdk_jar: str,
    java_home: str = "",
    scale: int = 2,
    max_fps: int = 30,
) -> None:
    """Start JVM, connect device, begin video stream. Idempotent.

    Args:
        serial: Device SN.
        sdk_jar: Path to SDK JAR.
        java_home: JDK/JRE path (auto-detect if empty).
        scale: Downscale factor. 1=original, 2=half, 3=third, etc.
        max_fps: Frame rate limit (1-60).
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
    global _jvm_started, _device, _cb_proxy
    if not _jvm_started:
        return
    try:
        if _device:
            _device.stopCaptureScreen()
    except Exception:
        pass
    try:
        import jpype; jpype.shutdownJVM()
    except Exception:
        pass
    _jvm_started = False
    _device = None
    _cb_proxy = None


def screenshot(wait_new: bool = False, timeout: float = 1.0) -> bytes:
    """Latest frame as PNG bytes (at output resolution).

    wait_new=True 时等待至少一帧新画面到达再返回 —— scrcpy 视频流在 JVM 线程
    异步更新 _latest_png, 点击后若立即读会取到过期缓存帧(识别到已消失的按钮,
    导致重复操作)。超时无新帧则警告(视频流可能停滞)。
    """
    if wait_new:
        start = _frame_count
        t0 = time.perf_counter()
        while _frame_count <= start and time.perf_counter() - t0 < timeout:
            time.sleep(0.01)
        if _frame_count <= start:
            log.warning("scrcpy: %.1fs 内无新帧, 视频流可能停滞", timeout)
    with _frame_lock:
        return _latest_png or b""


def screenshot_bgr() -> np.ndarray | None:
    """Latest frame as BGR numpy array (at output resolution)."""
    with _frame_lock:
        return _latest_bgr.copy() if _latest_bgr is not None else None


def resolution() -> tuple[int, int]:
    """Device native resolution (for coordinate mapping)."""
    return (_native_w, _native_h)


def output_resolution() -> tuple[int, int]:
    """Output resolution after scaling (for display/capture)."""
    return (_output_w, _output_h)


def touch(x: int, y: int, duration_ms: int = 50) -> float:
    """Tap at native pixel coordinates."""
    t0 = time.perf_counter()
    _device.onTouchDown(x, y)
    if duration_ms > 0:
        time.sleep(duration_ms / 1000.0)
    _device.onTouchUp(x, y)
    return (time.perf_counter() - t0) * 1000


def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 500) -> float:
    """Swipe at native pixel coordinates."""
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


# ── Internals ─────────────────────────────────────────────────────────────

def _start_jvm(sdk_jar: str, java_home: str):
    if java_home:
        os.environ["JAVA_HOME"] = java_home
    if not os.environ.get("JAVA_HOME"):
        # Search order: project-local → DevEco Studio → system
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        for d in [
            os.path.join(project_root, "tools", "jbr"),
            "D:/resource/jbr",
            "E:/DevEco Studio/jbr",
            "C:/DevEco Studio/jbr",
        ]:
            if os.path.isdir(d):
                os.environ["JAVA_HOME"] = d
                break

    import jpype, jpype.imports
    jh = os.environ.get("JAVA_HOME", "")
    jvm_path = None
    for dll in [f"{jh}/bin/server/jvm.dll", f"{jh}/jre/bin/server/jvm.dll"]:
        if os.path.exists(dll):
            jvm_path = dll
            break
    if not jvm_path:
        raise RuntimeError(f"JVM not found at {jh}")

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

                    # FPS limiting
                    now = time.perf_counter()
                    if frame_interval > 0 and now - last_frame_time < frame_interval:
                        continue
                    last_frame_time = now

                    # Detect resolution change (portrait↔landscape rotation)
                    if not _native_w or frame.width != _native_w or frame.height != _native_h:
                        _native_w, _native_h = frame.width, frame.height
                        _output_w, _output_h = _native_w // _scale, _native_h // _scale
                        log.info("Screen resolution: %dx%d → output %dx%d (scale=%d)",
                                 _native_w, _native_h, _output_w, _output_h, _scale)

                    # Convert + scale
                    bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGBA2BGR)
                    tw, th = _output_w, _output_h
                    if _scale > 1 and (bgr.shape[1], bgr.shape[0]) != (tw, th):
                        bgr = cv2.resize(bgr, (tw, th), interpolation=cv2.INTER_LINEAR)

                    _, png = cv2.imencode(".png", bgr)

                    with _frame_lock:
                        _latest_bgr = bgr
                        _latest_png = png.tobytes()
                        _frame_count += 1
                        if _frame_count % 30 == 0:
                            import hashlib
                            h = hashlib.md5(png.tobytes()).hexdigest()[:8]
                            log.info("stream frame %d hash=%s", _frame_count, h)
        except Exception:
            log.exception("on_data decode failed")

    def on_exception(t):
        log.warning("Stream: %s", t.getMessage() if t.getMessage() else str(t))

    def on_ready():
        _stream_ready.set()

    _cb_proxy = __import__("jpype").JProxy(
        "com.huawei.hosscrcpy.api.ScreenCapCallback",
        {"onData": on_data, "onException": on_exception, "onReady": on_ready},
    )

    log.info("Video stream: scale=%d @ %dfps", _scale, _max_fps)
    _device.startCaptureScreen(_cb_proxy)

    if not _stream_ready.wait(timeout=30):
        log.warning("Stream onReady not fired after 30s")

    # Wait for first decoded frame (SDK may retry gRPC connection internally)
    for attempt in range(120):  # up to 60 seconds
        with _frame_lock:
            if _frame_count > 0:
                break
        time.sleep(0.5)
        if attempt == 60:
            log.warning("Still waiting for first frame (SDK may be retrying)...")

    if _frame_count == 0:
        raise RuntimeError(
            "Video stream failed to deliver frames. "
            "Check device connection and retry."
        )

    log.info("Stream live: %d frames, native %dx%d",
             _frame_count, _native_w, _native_h)

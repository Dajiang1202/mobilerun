"""Scrcpy configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScrcpyConfig:
    """Configuration for scrcpy SDK bridge.

    Attributes:
        serial: Device serial number.
        sdk_jar: Path to HOScrcpy SDK JAR.
        java_home: Path to JDK/JRE. Auto-detected if empty.
        scale: Downscale factor for screenshot output.
            1 = original (1276×2848)
            2 = half     (638×1424)
            3 = third    (425×949)
            N = 1/N scale
        max_fps: Frame rate limit (1-60). Extra frames dropped client-side.
        bitrate: Video bitrate in bps (0=SDK default, ~8Mbps).
            建议: scale=2 用 2_000_000 (2Mbps); scale=1 用 4_000_000。
            越低越省功耗/带宽, 对截图质量影响不大(关键帧即可)。
        i_frame_interval: I帧间隔秒数 (0=SDK default).
            越大压缩率越高越省带宽, 但首帧/场景切换延迟增加。
            建议: 5 (每5秒一个关键帧, 备战阶段够用)。
    """
    serial: str = ""
    sdk_jar: str = ""
    java_home: str = ""
    scale: int = 2      # 1=original, 2=half, 3=third, ...
    max_fps: int = 30
    bitrate: int = 0            # 0=SDK default; 2_000_000=2Mbps low-power
    i_frame_interval: int = 0   # 0=SDK default; 5=every 5s

    @classmethod
    def from_dict(cls, d: dict | None) -> ScrcpyConfig:
        if not d:
            return cls()
        valid = {"serial", "sdk_jar", "java_home", "scale", "max_fps",
                 "bitrate", "i_frame_interval"}
        return cls(**{k: v for k, v in d.items() if k in valid and v is not None})

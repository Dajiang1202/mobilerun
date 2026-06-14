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
    """
    serial: str = ""
    sdk_jar: str = ""
    java_home: str = ""
    scale: int = 2      # 1=original, 2=half, 3=third, ...
    max_fps: int = 30

    @classmethod
    def from_dict(cls, d: dict | None) -> ScrcpyConfig:
        if not d:
            return cls()
        valid = {"serial", "sdk_jar", "java_home", "scale", "max_fps"}
        return cls(**{k: v for k, v in d.items() if k in valid and v is not None})

"""Device driver abstractions for Mobilerun."""

from mobilerun.tools.driver.android import AndroidDriver
from mobilerun.tools.driver.base import DeviceDisconnectedError, DeviceDriver
from mobilerun.tools.driver.cloud import CloudDriver
from mobilerun.tools.driver.ios import IOSDriver
from mobilerun.tools.driver.recording import RecordingDriver
from mobilerun.tools.driver.stealth import StealthDriver
from mobilerun.tools.driver.visual_remote import VisualRemoteDriver


def create_driver(
    device_config: "DeviceConfig",  # noqa: F821
) -> DeviceDriver:
    """Create the appropriate DeviceDriver from a DeviceConfig.

    Reads ``device_config.platform`` to select and instantiate the correct
    driver subclass (AndroidDriver, HarmonyOSDriver, or IOSDriver).

    Example:
        config = ConfigLoader.load()
        driver = create_driver(config.device)
        await driver.connect()
    """
    platform = (device_config.platform or "").lower()
    if platform == "ios":
        return IOSDriver(url=device_config.serial)
    if platform == "harmonyos":
        from mobilerun.tools.driver.harmonyos import HarmonyOSDriver  # lazy import

        return HarmonyOSDriver(
            serial=device_config.serial,
            hdc_path=getattr(device_config, "hdc_path", "hdc"),
            screenshot_method=getattr(
                device_config, "screenshot_method", "auto"
            ),
        )
    if platform not in ("", "android"):
        raise ValueError(
            f"Unsupported platform: {device_config.platform!r}. "
            "Expected 'android', 'harmonyos', or 'ios'."
        )
    # Default: Android
    return AndroidDriver(
        serial=device_config.serial,
        use_tcp=device_config.use_tcp,
    )


__all__ = [
    "DeviceDisconnectedError",
    "DeviceDriver",
    "AndroidDriver",
    "CloudDriver",
    "HarmonyOSDriver",
    "IOSDriver",
    "RecordingDriver",
    "StealthDriver",
    "VisualRemoteDriver",
    "create_driver",
]

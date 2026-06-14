"""Scrcpy exception hierarchy."""


class ScrcpyError(Exception):
    """Base for all scrcpy errors."""


class ScrcpyConnectionError(ScrcpyError):
    """Device not found or JVM failed to start."""


class ScrcpyStreamError(ScrcpyError):
    """Video stream failed to start or died."""


class ScrcpyTimeoutError(ScrcpyError):
    """Operation timed out."""

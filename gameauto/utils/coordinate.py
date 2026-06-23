"""Coordinate conversion utilities.

Uses normalized [0-1000] coordinates as the internal standard
(same as VLM output format). Also supports [0.0-1.0] for CV tasks.
"""

NORMALIZED_MAX = 1000


def to_absolute(nx: float, ny: float, width: int, height: int) -> tuple[int, int]:
    """Convert normalized [0-1000] coordinates to absolute pixels."""
    if width is None or height is None:
        raise ValueError("Screen dimensions not available")
    return int(nx * width / NORMALIZED_MAX), int(ny * height / NORMALIZED_MAX)


def to_normalized(px: int, py: int, width: int, height: int) -> tuple[int, int]:
    """Convert absolute pixels to normalized [0-1000] coordinates."""
    if width is None or height is None:
        raise ValueError("Screen dimensions not available")
    return int(px * NORMALIZED_MAX / width), int(py * NORMALIZED_MAX / height)


def unit_to_1000(u: float) -> int:
    """Convert [0.0-1.0] to [0-1000]."""
    return round(u * NORMALIZED_MAX)


def unit1000_to_unit(u: int) -> float:
    """Convert [0-1000] to [0.0-1.0]."""
    return u / NORMALIZED_MAX

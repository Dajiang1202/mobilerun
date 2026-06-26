"""Small image helpers used by screenshot-only device backends."""

from __future__ import annotations

import struct
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

MODEL_SCREENSHOT_MAX_SIDE = 1024


def image_dimensions(image: bytes) -> tuple[int, int]:
    """Return ``(width, height)`` for PNG or JPEG bytes."""
    # Fast path: try magic number first
    if image.startswith(b"\x89PNG\r\n\x1a\n") and len(image) >= 24:
        width, height = struct.unpack(">II", image[16:24])
        return int(width), int(height)

    if image.startswith(b"\xff\xd8"):
        try:
            return _jpeg_dimensions(image)
        except ValueError:
            pass

    # Fallback: use PIL for more robust parsing (handles edge cases and HarmonyOS screenshots)
    try:
        with Image.open(BytesIO(image)) as img:
            return img.width, img.height
    except Exception:
        pass

    raise ValueError("Unsupported screenshot image format. Expected PNG or JPEG.")


def fit_dimensions_to_max_side(
    width: int, height: int, max_side: int = MODEL_SCREENSHOT_MAX_SIDE
) -> tuple[int, int]:
    """Return dimensions scaled down so the longest side is at most ``max_side``."""
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive.")
    if max(width, height) <= max_side:
        return width, height

    scale = max_side / max(width, height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def resize_image_to_max_side(
    image: bytes, max_side: int = MODEL_SCREENSHOT_MAX_SIDE
) -> bytes:
    """Resize image bytes to the same coordinate space exposed to vision agents."""
    width, height = image_dimensions(image)
    target_width, target_height = fit_dimensions_to_max_side(width, height, max_side)
    if (target_width, target_height) == (width, height):
        return image

    with Image.open(BytesIO(image)) as source:
        resized = source.convert("RGB").resize(
            (target_width, target_height),
            Image.Resampling.LANCZOS,
        )
        output = BytesIO()
        resized.save(output, format="PNG")
        return output.getvalue()


def resize_image_to_max_side_with_grid(
    image: bytes,
    max_side: int = MODEL_SCREENSHOT_MAX_SIDE,
    divisions: int = 10,
    # use_normalized: True 时网格标签显示 [0-1000] 归一化坐标而非实际像素
    use_normalized: bool = False,
) -> bytes:
    """Resize image and overlay a coordinate grid.

    When *use_normalized* is True, grid labels show [0-1000] normalized values
    instead of pixel coordinates.
    """
    width, height = image_dimensions(image)
    target_width, target_height = fit_dimensions_to_max_side(width, height, max_side)

    with Image.open(BytesIO(image)) as source:
        screenshot = source.convert("RGBA")
        if (target_width, target_height) != (width, height):
            screenshot = screenshot.resize(
                (target_width, target_height),
                Image.Resampling.LANCZOS,
            )

        # 传递 use_normalized 参数以控制网格标签格式（像素 vs [0-1000]）
        _draw_coordinate_grid(
            screenshot, divisions=divisions, use_normalized=use_normalized
        )
        # Flatten RGBA → RGB (drop alpha channel — screenshots don't need it)
        rgb = Image.new("RGB", screenshot.size, (255, 255, 255))
        rgb.paste(screenshot, screenshot)
        output = BytesIO()
        rgb.save(output, format="PNG")
        return output.getvalue()


def _draw_coordinate_grid(
    image: Image.Image, divisions: int, use_normalized: bool = False  # 归一化坐标模式
) -> None:
    width, height = image.size
    if divisions <= 0 or width <= 0 or height <= 0:
        return

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default()

    line_color = (255, 255, 255, 68)
    major_line_color = (255, 230, 120, 110)
    label_fill = (255, 255, 255, 230)
    label_shadow = (0, 0, 0, 190)
    label_bg = (0, 0, 0, 115)

    max_norm = 1000  # 归一化坐标系最大值

    for index in range(divisions + 1):
        x = round(index * (width - 1) / divisions)
        y = round(index * (height - 1) / divisions)
        color = (
            major_line_color if index in {0, divisions // 2, divisions} else line_color
        )
        draw.line([(x, 0), (x, height - 1)], fill=color, width=1)
        draw.line([(0, y), (width - 1, y)], fill=color, width=1)

        # 归一化模式: 标签显示 0~1000 的相对坐标（分辨率无关）
        if use_normalized:
            x_label = f"x={round(index * max_norm / divisions)}"
            y_label = f"y={round(index * max_norm / divisions)}"
        else:
            x_label = f"x={x}"
            y_label = f"y={y}"

        _draw_grid_label(
            draw,
            x_label,
            (min(x + 3, width - 38), 4),
            font,
            label_fill,
            label_shadow,
            label_bg,
        )
        _draw_grid_label(
            draw,
            y_label,
            (4, min(y + 3, height - 14)),
            font,
            label_fill,
            label_shadow,
            label_bg,
        )

    image.alpha_composite(overlay)


def _draw_grid_label(
    draw: ImageDraw.ImageDraw,
    text: str,
    position: tuple[int, int],
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    shadow: tuple[int, int, int, int],
    background: tuple[int, int, int, int],
) -> None:
    x, y = position
    bbox = draw.textbbox((x, y), text, font=font)
    draw.rectangle(
        (bbox[0] - 2, bbox[1] - 1, bbox[2] + 2, bbox[3] + 1),
        fill=background,
    )
    draw.text((x + 1, y + 1), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


def _jpeg_dimensions(image: bytes) -> tuple[int, int]:
    offset = 2
    length = len(image)
    while offset + 9 < length:
        if image[offset] != 0xFF:
            offset += 1
            continue

        while offset < length and image[offset] == 0xFF:
            offset += 1
        if offset >= length:
            break

        marker = image[offset]
        offset += 1

        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA:
            break
        if offset + 2 > length:
            break

        segment_length = int.from_bytes(image[offset : offset + 2], "big")
        if segment_length < 2:
            raise ValueError("Invalid JPEG segment length.")

        if _is_start_of_frame(marker):
            if offset + 7 > length:
                break
            height = int.from_bytes(image[offset + 3 : offset + 5], "big")
            width = int.from_bytes(image[offset + 5 : offset + 7], "big")
            return int(width), int(height)

        offset += segment_length

    raise ValueError("Could not read JPEG dimensions.")


def _is_start_of_frame(marker: int) -> bool:
    return marker in {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }

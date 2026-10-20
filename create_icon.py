# -*- coding: utf-8 -*-
"""生成 FileOrganizer-Tool 的科技风应用图标。

脚本先使用 tkinter 的 Canvas 绘制图标，再把画布中的图元栅格化为
带透明通道的 PNG，并封装成包含多种尺寸的 app_icon.ico。
整个过程只使用 Python 标准库。
"""

from __future__ import annotations

import math
import struct
import tkinter as tk
import zlib
from pathlib import Path


CANVAS_SIZE = 256
SUPERSAMPLE = 3
ICON_SIZES = (256, 128, 64, 48, 32, 24, 16)


def hex_rgb(color: str) -> tuple[int, int, int]:
    """把 #RRGGBB 颜色转换成整数 RGB 三元组。"""
    color = color.lstrip("#")
    return tuple(int(color[index : index + 2], 16) for index in (0, 2, 4))


def mix_color(start: str, end: str, amount: float) -> str:
    """按比例混合两种颜色，用于绘制平滑渐变。"""
    start_rgb = hex_rgb(start)
    end_rgb = hex_rgb(end)
    rgb = tuple(
        round(start_value + (end_value - start_value) * amount)
        for start_value, end_value in zip(start_rgb, end_rgb)
    )
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def rounded_span(
    y: float,
    left: float,
    top: float,
    right: float,
    bottom: float,
    radius: float,
) -> tuple[float, float] | None:
    """返回圆角矩形在指定高度上的左右边界。"""
    if y < top or y >= bottom:
        return None

    inset = 0.0
    if y < top + radius:
        distance = top + radius - y
        inset = radius - math.sqrt(max(0.0, radius * radius - distance * distance))
    elif y >= bottom - radius:
        distance = y - (bottom - radius)
        inset = radius - math.sqrt(max(0.0, radius * radius - distance * distance))

    return left + inset, right - inset


def draw_rounded_gradient(
    canvas: tk.Canvas,
    left: float,
    top: float,
    right: float,
    bottom: float,
    radius: float,
    start_color: str,
    end_color: str,
) -> None:
    """用逐行短线绘制圆角矩形渐变。"""
    first_row = math.ceil(top)
    last_row = math.ceil(bottom)
    for y in range(first_row, last_row):
        span = rounded_span(y + 0.5, left, top, right, bottom, radius)
        if span is None:
            continue
        amount = (y + 0.5 - top) / (bottom - top)
        canvas.create_line(
            span[0],
            y,
            span[1],
            y,
            width=1,
            fill=mix_color(start_color, end_color, amount),
            capstyle=tk.BUTT,
        )


def rounded_rect_points(
    left: float,
    top: float,
    right: float,
    bottom: float,
    radius: float,
    steps: int = 8,
) -> list[float]:
    """生成可用于 Canvas polygon 的圆角矩形顶点。"""
    points: list[float] = []
    corners = (
        (left + radius, top + radius, math.pi, math.pi * 1.5),
        (right - radius, top + radius, math.pi * 1.5, math.pi * 2),
        (right - radius, bottom - radius, 0, math.pi * 0.5),
        (left + radius, bottom - radius, math.pi * 0.5, math.pi),
    )
    for center_x, center_y, start_angle, end_angle in corners:
        for step in range(steps + 1):
            angle = start_angle + (end_angle - start_angle) * step / steps
            points.extend(
                (
                    center_x + radius * math.cos(angle),
                    center_y + radius * math.sin(angle),
                )
            )
    return points


def sparkle_points(
    center_x: float,
    center_y: float,
    outer_radius: float,
    inner_radius: float,
) -> list[float]:
    """生成有四个尖角的魔法星芒。"""
    return [
        center_x,
        center_y - outer_radius,
        center_x + inner_radius,
        center_y - inner_radius,
        center_x + outer_radius,
        center_y,
        center_x + inner_radius,
        center_y + inner_radius,
        center_x,
        center_y + outer_radius,
        center_x - inner_radius,
        center_y + inner_radius,
        center_x - outer_radius,
        center_y,
        center_x - inner_radius,
        center_y - inner_radius,
    ]


def draw_arc(
    canvas: tk.Canvas,
    center_x: float,
    center_y: float,
    radius: float,
    start_degrees: float,
    end_degrees: float,
    color: str,
    width: float,
) -> None:
    """把圆弧拆成短线段，方便栅格化。"""
    segments = 36
    previous: tuple[float, float] | None = None
    for index in range(segments + 1):
        angle = math.radians(
            start_degrees + (end_degrees - start_degrees) * index / segments
        )
        point = (
            center_x + radius * math.cos(angle),
            center_y + radius * math.sin(angle),
        )
        if previous is not None:
            canvas.create_line(
                *previous,
                *point,
                fill=color,
                width=width,
                capstyle=tk.ROUND,
            )
        previous = point


def draw_icon(canvas: tk.Canvas) -> None:
    """在 tkinter 画布上绘制深蓝渐变、文件夹与魔法星芒。"""
    # 外层深色轮廓让图标在浅色和深色桌面上都保持清晰。
    canvas.create_polygon(
        rounded_rect_points(5, 8, 251, 252, 58),
        fill="#030D1A",
        outline="",
    )

    # 青色边框与深色底板构成简洁的科技感外框。
    draw_rounded_gradient(
        canvas, 10, 10, 246, 246, 54, "#45E6FF", "#166C9A"
    )
    draw_rounded_gradient(
        canvas, 13, 14, 243, 242, 50, "#09182C", "#0B2C50"
    )

    # 背后的文件夹标签与主体。
    canvas.create_polygon(
        rounded_rect_points(56, 81, 126, 120, 11),
        fill="#075E9E",
        outline="",
    )
    canvas.create_polygon(
        rounded_rect_points(50, 98, 207, 196, 18),
        fill="#086FAF",
        outline="",
    )
    canvas.create_polygon(
        64,
        101,
        122,
        101,
        139,
        120,
        64,
        120,
        fill="#086FAF",
        outline="",
    )

    # 右上角的轨道弧线为星芒增加一点动态感。
    draw_arc(canvas, 190, 74, 48, 205, 335, "#197EA5", 2)
    canvas.create_polygon(
        rounded_rect_points(48, 114, 209, 203, 16),
        fill="#0B4E94",
        outline="",
    )

    # 文件夹正面使用蓝青色渐变，模拟细微的玻璃质感。
    draw_rounded_gradient(
        canvas, 46, 113, 210, 202, 17, "#59DFFF", "#0B5CC8"
    )
    draw_rounded_gradient(
        canvas, 52, 128, 204, 197, 11, "#36BDF7", "#0A4CAB"
    )

    # 上层高光和底部暗面增强体积感。
    canvas.create_line(
        61,
        121,
        194,
        121,
        fill="#B7F7FF",
        width=2,
        capstyle=tk.ROUND,
    )
    canvas.create_line(
        58,
        128,
        58,
        178,
        fill="#71E8FF",
        width=2,
        capstyle=tk.ROUND,
    )
    canvas.create_line(
        63,
        192,
        190,
        192,
        fill="#083D88",
        width=3,
        capstyle=tk.ROUND,
    )

    # 主星芒由多层色块叠加，缩小后仍能保留清晰的亮点。
    canvas.create_polygon(
        sparkle_points(191, 74, 43, 10),
        fill="#093F64",
        outline="",
    )
    canvas.create_polygon(
        sparkle_points(191, 74, 35, 8),
        fill="#27CFFF",
        outline="",
    )
    canvas.create_polygon(
        sparkle_points(191, 74, 25, 5.6),
        fill="#B8FAFF",
        outline="",
    )
    canvas.create_polygon(
        sparkle_points(191, 74, 10, 2.4),
        fill="#FFFFFF",
        outline="",
    )

    # 两颗小星和光点让构图更完整。
    canvas.create_polygon(
        sparkle_points(61, 55, 13, 3),
        fill="#5DE7FF",
        outline="",
    )
    canvas.create_polygon(
        sparkle_points(61, 55, 6, 1.4),
        fill="#E7FFFF",
        outline="",
    )
    canvas.create_polygon(
        sparkle_points(222, 129, 9, 2),
        fill="#7DEBFF",
        outline="",
    )
    canvas.create_oval(
        211,
        53,
        219,
        61,
        fill="#B9FAFF",
        outline="",
    )
    canvas.create_oval(
        226,
        91,
        231,
        96,
        fill="#39CEEF",
        outline="",
    )


def fill_rect(
    pixels: bytearray,
    width: int,
    height: int,
    left: int,
    top: int,
    right: int,
    bottom: int,
    rgb: tuple[int, int, int],
) -> None:
    """用纯色填充整数矩形区域。"""
    left = max(0, left)
    top = max(0, top)
    right = min(width, right)
    bottom = min(height, bottom)
    if left >= right or top >= bottom:
        return

    row = bytes((*rgb, 255)) * (right - left)
    stride = width * 4
    for y in range(top, bottom):
        start = y * stride + left * 4
        pixels[start : start + len(row)] = row


def fill_polygon(
    pixels: bytearray,
    width: int,
    height: int,
    coordinates: list[float],
    rgb: tuple[int, int, int],
) -> None:
    """使用扫描线算法填充 Canvas polygon 图元。"""
    points = list(zip(coordinates[::2], coordinates[1::2]))
    if len(points) < 3:
        return

    min_y = max(0, math.floor(min(point[1] for point in points)))
    max_y = min(height, math.ceil(max(point[1] for point in points)))

    for y in range(min_y, max_y):
        scan_y = y + 0.5
        intersections: list[float] = []
        for index, (x1, y1) in enumerate(points):
            x2, y2 = points[(index + 1) % len(points)]
            if (y1 <= scan_y < y2) or (y2 <= scan_y < y1):
                ratio = (scan_y - y1) / (y2 - y1)
                intersections.append(x1 + (x2 - x1) * ratio)

        intersections.sort()
        for index in range(0, len(intersections) - 1, 2):
            left = max(0, math.ceil(intersections[index] - 0.5))
            right = min(width, math.ceil(intersections[index + 1] - 0.5))
            fill_rect(pixels, width, height, left, y, right, y + 1, rgb)


def fill_oval(
    pixels: bytearray,
    width: int,
    height: int,
    left: float,
    top: float,
    right: float,
    bottom: float,
    rgb: tuple[int, int, int],
) -> None:
    """填充 Canvas oval 图元。"""
    center_x = (left + right) / 2
    center_y = (top + bottom) / 2
    radius_x = max((right - left) / 2, 0.001)
    radius_y = max((bottom - top) / 2, 0.001)

    min_x = max(0, math.floor(left))
    max_x = min(width, math.ceil(right))
    min_y = max(0, math.floor(top))
    max_y = min(height, math.ceil(bottom))
    for y in range(min_y, max_y):
        normalized_y = ((y + 0.5) - center_y) / radius_y
        remaining = 1 - normalized_y * normalized_y
        if remaining < 0:
            continue
        half_span = radius_x * math.sqrt(remaining)
        row_left = max(0, math.ceil(center_x - half_span - 0.5))
        row_right = min(width, math.ceil(center_x + half_span - 0.5))
        fill_rect(pixels, width, height, row_left, y, row_right, y + 1, rgb)


def fill_line(
    pixels: bytearray,
    width: int,
    height: int,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    line_width: float,
    rgb: tuple[int, int, int],
    round_cap: bool,
) -> None:
    """填充一条线段图元。"""
    half_width = max(line_width, 1.0) / 2
    min_x = max(0, math.floor(min(x1, x2) - half_width))
    max_x = min(width, math.ceil(max(x1, x2) + half_width))
    min_y = max(0, math.floor(min(y1, y2) - half_width))
    max_y = min(height, math.ceil(max(y1, y2) + half_width))
    delta_x = x2 - x1
    delta_y = y2 - y1
    length_squared = delta_x * delta_x + delta_y * delta_y

    for y in range(min_y, max_y):
        pixel_y = y + 0.5
        for x in range(min_x, max_x):
            pixel_x = x + 0.5
            if length_squared == 0:
                distance = math.hypot(pixel_x - x1, pixel_y - y1)
                projection = 0.0
            else:
                projection = (
                    (pixel_x - x1) * delta_x + (pixel_y - y1) * delta_y
                ) / length_squared
                clamped = min(1.0, max(0.0, projection))
                nearest_x = x1 + clamped * delta_x
                nearest_y = y1 + clamped * delta_y
                distance = math.hypot(pixel_x - nearest_x, pixel_y - nearest_y)

            inside_projection = 0.0 <= projection <= 1.0 or round_cap
            if inside_projection and distance <= half_width:
                offset = (y * width + x) * 4
                pixels[offset : offset + 4] = bytes((*rgb, 255))


def rasterize_canvas(canvas: tk.Canvas) -> bytearray:
    """把 tkinter Canvas 中的图元渲染为 RGBA 像素。"""
    width = CANVAS_SIZE * SUPERSAMPLE
    height = width
    pixels = bytearray(width * height * 4)

    for item in canvas.find_all():
        item_type = canvas.type(item)
        if item_type not in {"line", "polygon", "rectangle", "oval"}:
            continue
        if canvas.itemcget(item, "state") == "hidden":
            continue

        fill = canvas.itemcget(item, "fill")
        if not fill:
            continue
        rgb = hex_rgb(fill)
        coordinates = [
            float(value) * SUPERSAMPLE for value in canvas.coords(item)
        ]

        if item_type == "line":
            line_width = float(canvas.itemcget(item, "width")) * SUPERSAMPLE
            round_cap = canvas.itemcget(item, "capstyle") == "round"
            for index in range(0, len(coordinates) - 2, 2):
                fill_line(
                    pixels,
                    width,
                    height,
                    coordinates[index],
                    coordinates[index + 1],
                    coordinates[index + 2],
                    coordinates[index + 3],
                    line_width,
                    rgb,
                    round_cap,
                )
        elif item_type == "polygon":
            fill_polygon(pixels, width, height, coordinates, rgb)
        elif item_type == "rectangle":
            fill_rect(
                pixels,
                width,
                height,
                math.floor(coordinates[0]),
                math.floor(coordinates[1]),
                math.ceil(coordinates[2]),
                math.ceil(coordinates[3]),
                rgb,
            )
        elif item_type == "oval":
            fill_oval(
                pixels,
                width,
                height,
                coordinates[0],
                coordinates[1],
                coordinates[2],
                coordinates[3],
                rgb,
            )

    return pixels


def downsample(
    pixels: bytearray,
    source_size: int,
    target_size: int,
) -> bytearray:
    """使用区域平均法缩小图像，得到平滑且无黑边的透明图标。"""
    if source_size % target_size != 0:
        raise ValueError("源图像尺寸必须能被目标尺寸整除")

    factor = source_size // target_size
    result = bytearray(target_size * target_size * 4)
    for target_y in range(target_size):
        for target_x in range(target_size):
            alpha_sum = 0
            red_sum = 0
            green_sum = 0
            blue_sum = 0
            for source_y in range(target_y * factor, (target_y + 1) * factor):
                for source_x in range(target_x * factor, (target_x + 1) * factor):
                    offset = (source_y * source_size + source_x) * 4
                    alpha = pixels[offset + 3]
                    alpha_sum += alpha
                    red_sum += pixels[offset] * alpha
                    green_sum += pixels[offset + 1] * alpha
                    blue_sum += pixels[offset + 2] * alpha

            if alpha_sum:
                red = round(red_sum / alpha_sum)
                green = round(green_sum / alpha_sum)
                blue = round(blue_sum / alpha_sum)
                alpha = round(alpha_sum / (factor * factor))
            else:
                red = green = blue = alpha = 0

            offset = (target_y * target_size + target_x) * 4
            result[offset : offset + 4] = bytes((red, green, blue, alpha))
    return result


def png_bytes(width: int, height: int, pixels: bytearray) -> bytes:
    """把 RGBA 像素编码成 PNG，供 ICO 文件内嵌。"""
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw.extend(pixels[y * stride : (y + 1) * stride])

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), level=9))
        + chunk(b"IEND", b"")
    )


def write_ico(path: Path, source_pixels: bytearray) -> None:
    """把不同尺寸的 PNG 封装成一个 ICO 文件。"""
    payloads: list[tuple[int, bytes]] = []
    for size in ICON_SIZES:
        resized = downsample(source_pixels, CANVAS_SIZE * SUPERSAMPLE, size)
        payloads.append((size, png_bytes(size, size, resized)))

    header = struct.pack("<HHH", 0, 1, len(payloads))
    directory = bytearray()
    offset = len(header) + 16 * len(payloads)
    for size, payload in payloads:
        stored_size = 0 if size == 256 else size
        directory.extend(
            struct.pack(
                "<BBBBHHII",
                stored_size,
                stored_size,
                0,
                0,
                1,
                32,
                len(payload),
                offset,
            )
        )
        offset += len(payload)

    path.write_bytes(header + directory + b"".join(payload for _, payload in payloads))


def main() -> None:
    """创建隐藏的 tkinter 画布、生成图标并打印结果。"""
    root = tk.Tk()
    try:
        root.withdraw()
        canvas = tk.Canvas(
            root,
            width=CANVAS_SIZE,
            height=CANVAS_SIZE,
            highlightthickness=0,
            background="#000000",
        )
        canvas.pack()
        draw_icon(canvas)
        root.update_idletasks()

        pixels = rasterize_canvas(canvas)
        output_path = Path(__file__).resolve().parent / "app_icon.ico"
        write_ico(output_path, pixels)
    finally:
        root.destroy()

    print("图标 app_icon.ico 已成功生成！")


if __name__ == "__main__":
    main()

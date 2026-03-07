from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "resources"

BG = (15, 123, 108, 255)
FG = (255, 255, 255, 255)
TRANSPARENT = (0, 0, 0, 0)


def clamp(value: float) -> int:
    return max(0, min(255, int(round(value))))


def in_rounded_rect(x: float, y: float, size: float, radius: float) -> bool:
    x = max(0.0, min(size, x))
    y = max(0.0, min(size, y))
    inner_left = radius
    inner_top = radius
    inner_right = size - radius
    inner_bottom = size - radius
    if inner_left <= x <= inner_right or inner_top <= y <= inner_bottom:
        return True
    corners = (
        (inner_left, inner_top),
        (inner_right, inner_top),
        (inner_left, inner_bottom),
        (inner_right, inner_bottom),
    )
    return any((x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2 for cx, cy in corners)


def in_ring(x: float, y: float, cx: float, cy: float, outer: float, inner: float) -> bool:
    distance = (x - cx) ** 2 + (y - cy) ** 2
    return inner ** 2 <= distance <= outer ** 2


def pixel_for(x: float, y: float, size: float) -> tuple[int, int, int, int]:
    if not in_rounded_rect(x, y, size, size * 0.22):
        return TRANSPARENT

    left = in_ring(x, y, size * 0.36, size * 0.52, size * 0.18, size * 0.095)
    right = in_ring(x, y, size * 0.64, size * 0.52, size * 0.18, size * 0.095)
    bridge = abs(y - size * 0.52) <= size * 0.034 and size * 0.44 <= x <= size * 0.56

    if left or right or bridge:
        return FG
    return BG


def render_icon(size: int, supersample: int = 4) -> bytes:
    hi = size * supersample
    pixels = []
    for y in range(size):
        for x in range(size):
            accum = [0, 0, 0, 0]
            for sy in range(supersample):
                for sx in range(supersample):
                    px = (x * supersample + sx + 0.5) / supersample
                    py = (y * supersample + sy + 0.5) / supersample
                    color = pixel_for(px, py, float(size))
                    for index, value in enumerate(color):
                        accum[index] += value
            scale = supersample * supersample
            pixels.extend(clamp(value / scale) for value in accum)
    return bytes(pixels)


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def encode_png(size: int, rgba: bytes) -> bytes:
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = png_chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    scanlines = bytearray()
    stride = size * 4
    for row in range(size):
        scanlines.append(0)
        start = row * stride
        scanlines.extend(rgba[start : start + stride])
    idat = png_chunk(b"IDAT", zlib.compress(bytes(scanlines), level=9))
    iend = png_chunk(b"IEND", b"")
    return header + ihdr + idat + iend


def write_ico(path: Path, sizes: list[int]) -> None:
    png_payloads = []
    for size in sizes:
        png_payloads.append((size, encode_png(size, render_icon(size))))

    header = struct.pack("<HHH", 0, 1, len(png_payloads))
    directory = bytearray()
    offset = 6 + 16 * len(png_payloads)
    blobs = bytearray()
    for size, payload in png_payloads:
        width = 0 if size >= 256 else size
        height = 0 if size >= 256 else size
        directory.extend(struct.pack("<BBBBHHII", width, height, 0, 0, 1, 32, len(payload), offset))
        blobs.extend(payload)
        offset += len(payload)
    path.write_bytes(header + bytes(directory) + bytes(blobs))


def main() -> None:
    RESOURCES.mkdir(parents=True, exist_ok=True)
    for size, name in ((256, "app_icon.png"), (32, "app_icon_32.png")):
        (RESOURCES / name).write_bytes(encode_png(size, render_icon(size)))
    write_ico(RESOURCES / "app_icon.ico", [16, 32, 48, 64, 128, 256])


if __name__ == "__main__":
    main()

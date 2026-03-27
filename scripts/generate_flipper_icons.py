#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import struct
import zlib


FLIPPER_DIR = Path(__file__).resolve().parents[1] / "flipper"
APP_ICON = FLIPPER_DIR / "radar_ai_10px.png"
IMAGE_DIR = FLIPPER_DIR / "images"

APP_ICON_ROWS = [
    "..##......",
    ".#..##....",
    "#.....#...",
    "..###..#..",
    ".##.##.#..",
    ".#.###.#..",
    ".#....#...",
    "..#..#....",
    "...##.....",
    "......##..",
]

PROVIDER_ICONS: dict[str, list[str]] = {
    "chatgpt_12x12.png": [
        "....##......",
        "...#..#.....",
        "..#....#....",
        ".#..##..#...",
        ".#.####.#...",
        "..##..##....",
        ".#.####.#...",
        ".#..##..#...",
        "..#....#....",
        "...#..#.....",
        "....##......",
        "............",
    ],
    "claude_12x12.png": [
        "...#####....",
        "..##...##...",
        ".##.........",
        ".##.........",
        ".##..###....",
        ".##...##....",
        ".##...##....",
        ".##...##....",
        ".##.........",
        ".##.........",
        "..##...##...",
        "...#####....",
    ],
    "codex_12x12.png": [
        "..##....##..",
        ".###....###.",
        "####....####",
        ".###....###.",
        "..###..###..",
        "...######...",
        "...######...",
        "..###..###..",
        ".###....###.",
        "####....####",
        ".###....###.",
        "..##....##..",
    ],
    "cursor_12x12.png": [
        "..##........",
        "..###.......",
        "..####......",
        "..#####.....",
        "..##.###....",
        "..##..###...",
        "..##...###..",
        "..##..###...",
        "..##.###....",
        "..#####.....",
        "..###.......",
        "..##........",
    ],
    "gemini_12x12.png": [
        ".....##.....",
        "....####....",
        "...######...",
        "..##.##.##..",
        ".##..##..##.",
        "############",
        "############",
        ".##..##..##.",
        "..##.##.##..",
        "...######...",
        "....####....",
        ".....##.....",
    ],
    "bot_12x12.png": [
        "....####....",
        "...#....#...",
        "..#.#..#.#..",
        "..#......#..",
        "..########..",
        "..#.#..#.#..",
        "..#......#..",
        "..#..##..#..",
        "..########..",
        "...#....#...",
        "....#..#....",
        "............",
    ],
}


def _trim_rows(rows: list[str], border: int = 1) -> list[str]:
    return [row[border:-border] for row in rows[border:-border]]


def chunk(name: bytes, data: bytes) -> bytes:
    body = name + data
    return (
        struct.pack(">I", len(data))
        + body
        + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    )


def png_from_rows(rows: list[str]) -> bytes:
    height = len(rows)
    width = len(rows[0])
    raw = bytearray()

    for row in rows:
        raw.append(0)
        for pixel in row:
            if pixel == "#":
                raw.extend((0, 0, 0))
            else:
                raw.extend((255, 255, 255))

    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(
                b"IHDR",
                struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
            ),
            chunk(b"IDAT", zlib.compress(bytes(raw), level=9)),
            chunk(b"IEND", b""),
        ]
    )


def main() -> int:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    app_icon_png = png_from_rows(APP_ICON_ROWS)
    APP_ICON.write_bytes(app_icon_png)
    (IMAGE_DIR / "radar_ai_10px.png").write_bytes(app_icon_png)
    for filename, rows in PROVIDER_ICONS.items():
        (IMAGE_DIR / filename).write_bytes(png_from_rows(rows))
        if filename.endswith("_12x12.png"):
            small_name = filename.replace("_12x12.png", "_10x10.png")
            (IMAGE_DIR / small_name).write_bytes(png_from_rows(_trim_rows(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

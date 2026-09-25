"""Regenerate the committed Signal PNG and multi-size Windows icon."""
from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtWidgets import QApplication

from codex_companion.branding import render_mark


ASSETS = Path(__file__).resolve().parent.parent / "assets"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(size: int) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    if not render_mark(size).save(buffer, "PNG"):
        raise RuntimeError(f"Could not render the {size}px icon")
    buffer.close()
    return bytes(data)


def main() -> None:
    app = QApplication.instance() or QApplication([])
    ASSETS.mkdir(exist_ok=True)
    images = [(size, png_bytes(size)) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + len(images) * 16
    entries = []
    for size, payload in images:
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0,
                                   1, 32, len(payload), offset))
        offset += len(payload)
    (ASSETS / "companion.ico").write_bytes(
        header + b"".join(entries) + b"".join(payload for _, payload in images))
    (ASSETS / "companion.png").write_bytes(png_bytes(256))
    print(f"Wrote {ASSETS / 'companion.ico'} and {ASSETS / 'companion.png'}")
    del app


if __name__ == "__main__":
    main()

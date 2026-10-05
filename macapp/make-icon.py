"""Draw the app icon (three document lines, the middle one a redaction bar) and build AppIcon.icns.

Run with any Python that has Pillow:  python make-icon.py Resources/AppIcon.icns
Proportions come from the Claude Design handoff (screen 10).
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024


def draw() -> Image.Image:
    # 824 px squircle on a 1024 canvas leaves the margin macOS icons use
    body, radius = 824, 185
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    x0 = (S - body) // 2
    grad = Image.new("RGBA", (body, body))
    top, bottom = (0xFC, 0xF9, 0xF4), (0xEB, 0xE3, 0xD7)
    gd = ImageDraw.Draw(grad)
    for y in range(body):
        t = y / (body - 1)
        gd.line([(0, y), (body, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    mask = Image.new("L", (body, body), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, body - 1, body - 1], radius, fill=255)
    img.paste(grad, (x0, x0), mask)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([x0, x0, x0 + body - 1, x0 + body - 1], radius, outline=(0xCF, 0xC3, 0xB2, 255), width=4)

    inner_x, inner_w = x0 + body * 0.2, body * 0.6
    unit = body / 72  # design was drawn on a 72 px icon
    bars = [(4.5, 0.74, (0xCF, 0xC3, 0xB2)), (11.7, 1.0, (0xAD, 0x53, 0x37)), (4.5, 0.56, (0xCF, 0xC3, 0xB2))]
    gap = 7.2 * unit
    total = sum(h * unit for h, _, _ in bars) + gap * 2
    y = x0 + (body - total) / 2
    for h, w, color in bars:
        h *= unit
        d.rounded_rectangle([inner_x, y, inner_x + inner_w * w, y + h], h * 0.24, fill=color + (255,))
        y += h + gap
    return img


def main(out: str) -> None:
    icon = draw()
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            icon.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
            icon.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", out], check=True)


if __name__ == "__main__":
    main(sys.argv[1])

"""Generate deterministic PNG/ICO renderings matching the SVG icon sources."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "build" / "icons"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def rounded(draw: ImageDraw.ImageDraw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def editor_icon(size: int) -> Image.Image:
    scale = size / 256
    im = Image.new("RGBA", (size, size), "#102A43")
    draw = ImageDraw.Draw(im)
    rounded(draw, (0, 0, size - 1, size - 1), 52 * scale, "#102A43")
    draw.line((20 * scale, 104 * scale, 54 * scale, 104 * scale), fill="#64E1D5", width=max(1, round(14 * scale)))
    draw.line((202 * scale, 152 * scale, 236 * scale, 152 * scale), fill="#64E1D5", width=max(1, round(14 * scale)))
    rounded(draw, tuple(x * scale for x in (54, 52, 202, 204)), 24 * scale, "#23888D", "#D8FFF9", max(1, round(8 * scale)))
    rounded(draw, tuple(x * scale for x in (76, 76, 180, 104)), 14 * scale, "#D8FFF9")
    for line in ((84, 130, 172, 130), (84, 154, 146, 154)):
        draw.line(tuple(x * scale for x in line), fill="#D8FFF9", width=max(1, round(12 * scale)))
    for x, y in ((54, 104), (202, 152), (174, 174)):
        r = max(1, round(12 * scale))
        draw.ellipse((x * scale - r, y * scale - r, x * scale + r, y * scale + r), fill="#F6C85F")
    return im


def host_icon(size: int) -> Image.Image:
    scale = size / 256
    im = Image.new("RGBA", (size, size), "#102A43")
    draw = ImageDraw.Draw(im)
    rounded(draw, (0, 0, size - 1, size - 1), 52 * scale, "#102A43")
    rounded(
        draw,
        tuple(x * scale for x in (42, 70, 168, 196)),
        22 * scale,
        "#228A91",
        "#D8FFF9",
        max(1, round(8 * scale)),
    )
    draw.line(
        tuple(x * scale for x in (64, 112, 146, 112, 146, 154)),
        fill="#D8FFF9",
        width=max(1, round(10 * scale)),
    )
    for x, y in ((64, 112), (146, 154)):
        radius = max(1, round(12 * scale))
        draw.ellipse(
            (
                x * scale - radius,
                y * scale - radius,
                x * scale + radius,
                y * scale + radius,
            ),
            fill="#F6C85F",
        )
    draw.arc(
        tuple(x * scale for x in (126, 59, 238, 171)),
        285,
        360,
        fill="#64E1D5",
        width=max(1, round(13 * scale)),
    )
    draw.arc(
        tuple(x * scale for x in (96, 29, 268, 201)),
        285,
        360,
        fill="#64E1D5",
        width=max(1, round(13 * scale)),
    )
    return im


def write_family(name: str, factory) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    frames = [factory(size) for size in SIZES]
    frames[-1].save(OUTPUT / f"{name}.png")
    frames[-1].save(
        OUTPUT / f"{name}.ico",
        format="ICO",
        append_images=frames[:-1],
        sizes=[(size, size) for size in SIZES],
    )


if __name__ == "__main__":
    write_family("L2DConfigEditor", editor_icon)
    write_family("L2DUpdateHost", host_icon)
    print(OUTPUT)

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate the integration's brand images.

The artwork here is original: WheresTheBus publishes only a trademarked
wordmark, which is both the wrong shape for Home Assistant's square icon and
not ours to redistribute under this repository's licence.  A school-bus badge
identifies the integration without borrowing anyone's mark.

Renders at 4x and downsamples, since PIL's drawing primitives are not
antialiased.

    python3 scripts/make_brand_assets.py
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw

SUPERSAMPLE = 4
SIZE = 512
CANVAS = SIZE * SUPERSAMPLE

BUS_YELLOW = (255, 199, 44, 255)
INK = (31, 41, 55, 255)
GLASS = (255, 255, 255, 255)

OUT = pathlib.Path(__file__).resolve().parent.parent / "brand"


def _s(value: float) -> int:
    """Scale a 512-space coordinate onto the supersampled canvas."""
    return round(value * SUPERSAMPLE)


def draw_icon() -> Image.Image:
    """Draw the square badge: a school bus on a rounded yellow tile."""
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        (0, 0, CANVAS - 1, CANVAS - 1), radius=_s(115), fill=BUS_YELLOW
    )

    # Body, with the roof carried over a short bonnet at the front.
    draw.rounded_rectangle((_s(74), _s(170), _s(438), _s(348)), radius=_s(34), fill=INK)

    # Windows: three side panes plus a windscreen at the front.
    for left in (108, 178, 248):
        draw.rounded_rectangle(
            (_s(left), _s(202), _s(left + 52), _s(268)), radius=_s(12), fill=GLASS
        )
    draw.rounded_rectangle(
        (_s(330), _s(202), _s(408), _s(268)), radius=_s(12), fill=GLASS
    )

    # Wheels, sitting proud of the body.
    for centre in (150, 360):
        draw.ellipse((_s(centre - 42), _s(316), _s(centre + 42), _s(400)), fill=INK)
        draw.ellipse(
            (_s(centre - 18), _s(340), _s(centre + 18), _s(376)), fill=BUS_YELLOW
        )

    # Headlight and bumper, so the front reads as the front.
    draw.rounded_rectangle(
        (_s(410), _s(292), _s(438), _s(312)), radius=_s(8), fill=GLASS
    )

    return image.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    """Write icon and logo at 1x and 2x."""
    OUT.mkdir(exist_ok=True)
    icon = draw_icon()

    icon.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png")
    icon.save(OUT / "icon@2x.png")
    # Home Assistant accepts a square logo when a brand has no wordmark of
    # its own; these are the same mark at the logo sizes.
    icon.resize((256, 256), Image.LANCZOS).save(OUT / "logo.png")
    icon.save(OUT / "logo@2x.png")

    for path in sorted(OUT.glob("*.png")):
        with Image.open(path) as opened:
            print(f"{path.name:<14} {opened.size[0]}x{opened.size[1]}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate Team Hub's home-screen icons from the round Chick-fil-A logo.

Usage:
    python scripts/generate_app_icons.py

Reads assets/cfa-round-logo.png (the official logo Josh supplied — copied
byte-for-byte, scaled only, never recoloured or redrawn) and writes to
static/teamhub/:
    logo-round.png             - the logo itself, for the masthead
    icon-180.png               - apple-touch-icon (iOS fills transparency with
                                 black, so the logo sits on white)
    icon-192.png, icon-512.png - web app manifest icons

Requires: pip install pillow
"""

import os
import shutil

from PIL import Image

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGO = os.path.join(REPO_ROOT, "assets", "cfa-round-logo.png")
OUT_DIR = os.path.join(REPO_ROOT, "static", "teamhub")
LOGO_SHARE = 0.74            # the logo's width as a share of the icon


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    shutil.copyfile(LOGO, os.path.join(OUT_DIR, "logo-round.png"))
    logo = Image.open(LOGO).convert("RGBA")
    for size in (180, 192, 512):
        icon = Image.new("RGBA", (size, size), (255, 255, 255, 255))
        side = round(size * LOGO_SHARE)
        scaled = logo.resize((side, round(side * logo.height / logo.width)), Image.LANCZOS)
        icon.alpha_composite(scaled, ((size - scaled.width) // 2, (size - scaled.height) // 2))
        path = os.path.join(OUT_DIR, f"icon-{size}.png")
        icon.convert("RGB").save(path, optimize=True)
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()

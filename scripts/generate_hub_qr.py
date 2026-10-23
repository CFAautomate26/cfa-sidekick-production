#!/usr/bin/env python3
"""Generate the Chick-fil-A branded Team Hub QR code and poster.

Usage:
    python scripts/generate_hub_qr.py [HUB_URL]

HUB_URL defaults to the production /hub URL on Render. Outputs are written
to assets/hub-qr/ (hub-qr.png, hub-poster.png, hub-poster.pdf) using the
same look as the Shift Lead poster — see generate_shift_qr.py for fonts.

Requires: pip install qrcode pillow
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import generate_shift_qr as shiftqr  # noqa: E402

DEFAULT_URL = "https://cfa-sidekick-production-4hx7.onrender.com/hub"
OUT_DIR = os.path.join(shiftqr.REPO_ROOT, "assets", "hub-qr")
HUB_TEXT = {
    "eyebrow": "HERE'S HOW WE DO IT",
    "title": "TEAM HUB",
    "body": ("Systems, processes, training and policies —",
             "how we do it at our restaurant,",
             "right on your phone."),
    "hints": ("Sign in with your first name + clock-in number",
              "No access yet? Ask a leader."),
}


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    os.makedirs(OUT_DIR, exist_ok=True)
    qr_img = shiftqr.build_qr(url)
    qr_path = os.path.join(OUT_DIR, "hub-qr.png")
    qr_img.save(qr_path)
    poster = shiftqr.build_poster(qr_img, HUB_TEXT)
    poster_path = os.path.join(OUT_DIR, "hub-poster.png")
    poster.save(poster_path, dpi=(300, 300))
    pdf_path = os.path.join(OUT_DIR, "hub-poster.pdf")
    poster.save(pdf_path, "PDF", resolution=300)
    print(f"QR target: {url}")
    print(f"Wrote {qr_path}")
    print(f"Wrote {poster_path}")
    print(f"Wrote {pdf_path}")


if __name__ == "__main__":
    main()

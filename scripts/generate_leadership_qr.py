#!/usr/bin/env python3
"""Generate the Chick-fil-A branded leadership application QR code and poster.

Usage:
    python scripts/generate_leadership_qr.py [FORM_URL]

FORM_URL defaults to the production Render URL of the /apply form. Outputs are
written to assets/leadership-qr/:
    leadership-qr.png      - branded QR code (logo centre, CFA red modules)
    leadership-poster.png  - print-ready 8.5x11 poster (300 DPI) with the QR

Requires: pip install qrcode pillow
The Apercu font files are licensed to CFA operators and are not committed to
this repo; point APERCU_DIR at a folder containing Apercu-Bold.otf /
Apercu-Medium.otf / Apercu-Regular.otf to use them, otherwise a system font
fallback is used.
"""

import os
import sys

import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer
from qrcode.image.styles.colormasks import SolidFillColorMask
from PIL import Image, ImageDraw, ImageFont

CFA_RED = (227, 25, 55)
CREAM = (255, 250, 242)
INK = (60, 44, 33)
SOFT = (150, 130, 112)
WHITE = (255, 255, 255)

DEFAULT_URL = "https://cfa-sidekick-production.onrender.com/apply"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO_ROOT, "assets", "leadership-qr")
LOGO_PATH = os.environ.get(
    "CFA_LOGO", os.path.join(REPO_ROOT, "assets", "cfa-logo.png"))
APERCU_DIR = os.environ.get("APERCU_DIR", "")


def load_font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    candidates = []
    if APERCU_DIR:
        candidates.append(os.path.join(APERCU_DIR, f"Apercu-{weight}.otf"))
    fallback = {
        "Bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "Medium": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "Regular": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    }[weight]
    candidates.append(fallback)
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def build_qr(url: str) -> Image.Image:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=40,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=RoundedModuleDrawer(),
        color_mask=SolidFillColorMask(back_color=WHITE, front_color=CFA_RED),
    ).convert("RGBA")

    # White rounded badge in the centre carrying the store logo. Error
    # correction H tolerates the occlusion.
    logo = Image.open(LOGO_PATH).convert("RGBA")
    badge_w = int(img.width * 0.30)
    badge_h = int(img.width * 0.17)
    radius = badge_h // 4
    badge = Image.new("RGBA", (badge_w, badge_h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(badge)
    bd.rounded_rectangle([0, 0, badge_w - 1, badge_h - 1], radius=radius,
                         fill=WHITE + (255,))
    pad = int(badge_h * 0.12)
    max_w, max_h = badge_w - 2 * pad, badge_h - 2 * pad
    scale = min(max_w / logo.width, max_h / logo.height)
    logo_r = logo.resize((int(logo.width * scale), int(logo.height * scale)),
                         Image.LANCZOS)
    badge.alpha_composite(
        logo_r, ((badge_w - logo_r.width) // 2, (badge_h - logo_r.height) // 2))
    img.alpha_composite(
        badge, ((img.width - badge_w) // 2, (img.height - badge_h) // 2))
    return img


def centred_text(draw, y, text, font, fill, width):
    box = draw.textbbox((0, 0), text, font=font)
    w = box[2] - box[0]
    draw.text(((width - w) // 2, y), text, font=font, fill=fill)
    return y + (box[3] - box[1])


def build_poster(qr_img: Image.Image) -> Image.Image:
    W, H = 2550, 3300  # 8.5x11" at 300 DPI
    poster = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(poster)

    banner_h = 480
    d.rectangle([0, 0, W, banner_h], fill=CFA_RED)
    f_eyebrow = load_font("Medium", 64)
    f_title = load_font("Bold", 150)
    f_store = load_font("Medium", 72)
    f_body = load_font("Regular", 66)
    f_scan = load_font("Bold", 84)
    f_foot = load_font("Regular", 56)

    y = 90
    y = centred_text(d, y, "GROW WITH US", f_eyebrow, (255, 255, 255), W) + 28
    centred_text(d, y, "JOIN OUR LEADERSHIP TEAM", f_title, WHITE, W)

    logo = Image.open(LOGO_PATH).convert("RGBA")
    scale = 620 / logo.width
    logo_r = logo.resize((620, int(logo.height * scale)), Image.LANCZOS)
    poster.paste(logo_r, ((W - logo_r.width) // 2, 590), logo_r)

    y = 590 + logo_r.height + 40
    y = centred_text(d, y, "WHARNCLIFFE & WONDERLAND", f_store, CFA_RED, W) + 70

    for line in (
        "Ready to take the next step? We are looking for team",
        "members who lead with care, own the standard, and",
        "serve with a second-mile spirit.",
    ):
        y = centred_text(d, y, line, f_body, INK, W) + 26

    qr_size = 1150
    qr_r = qr_img.resize((qr_size, qr_size), Image.LANCZOS)
    qr_card = Image.new("RGB", (qr_size + 80, qr_size + 80), WHITE)
    qr_card.paste(qr_r, (40, 40), qr_r)
    qy = y + 60
    poster.paste(qr_card, ((W - qr_card.width) // 2, qy))
    d.rounded_rectangle(
        [(W - qr_card.width) // 2, qy,
         (W + qr_card.width) // 2, qy + qr_card.height],
        radius=40, outline=CFA_RED, width=8)

    y = qy + qr_card.height + 70
    y = centred_text(d, y, "SCAN TO APPLY", f_scan, CFA_RED, W) + 30
    centred_text(d, y, "Applications go directly and confidentially to the Operator.",
                 f_foot, SOFT, W)

    foot_h = 170
    d.rectangle([0, H - foot_h, W, H], fill=CFA_RED)
    fb = load_font("Medium", 58)
    box = d.textbbox((0, 0), "Chick-fil-A Wharncliffe & Wonderland · 3459 Wonderland Road South, London, ON", font=fb)
    d.text(((W - (box[2] - box[0])) // 2, H - foot_h + (foot_h - (box[3] - box[1])) // 2),
           "Chick-fil-A Wharncliffe & Wonderland · 3459 Wonderland Road South, London, ON",
           font=fb, fill=WHITE)
    return poster


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    os.makedirs(OUT_DIR, exist_ok=True)
    qr_img = build_qr(url)
    qr_path = os.path.join(OUT_DIR, "leadership-qr.png")
    qr_img.save(qr_path)
    poster = build_poster(qr_img)
    poster_path = os.path.join(OUT_DIR, "leadership-poster.png")
    poster.save(poster_path, dpi=(300, 300))
    print(f"QR target: {url}")
    print(f"Wrote {qr_path}")
    print(f"Wrote {poster_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate the Chick-fil-A branded Life Planning Workshop QR code and poster.

Usage:
    python scripts/generate_life_planning_poster.py [SIGNUP_URL]

SIGNUP_URL defaults to the production Render URL of the /life-planning
sign-up form. Outputs are written to assets/life-planning-qr/:
    life-planning-qr.png      - branded QR code (logo centre, CFA red modules)
    life-planning-poster.png  - print-ready 8.5x11 poster (300 DPI) with the
                                workshop summary, commitment, and QR

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

DEFAULT_URL = "https://cfa-sidekick-production.onrender.com/life-planning"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO_ROOT, "assets", "life-planning-qr")
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


def wrap_text(draw, text, font, max_width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        box = draw.textbbox((0, 0), trial, font=font)
        if box[2] - box[0] <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


COMMITMENT = [
    "7 weeks with one life planning section covered each week",
    "Direct one on one mentorship from the Operator",
    "Weekly meetings for all 7 weeks. No lates, rescheduling, or misses (excluding emergencies)",
    "Complete your worksheet before each meeting to maximize your growth",
    "7 spots per group. Once this group completes the workshop, a new 7 week group will launch",
]


def build_poster(qr_img: Image.Image) -> Image.Image:
    W, H = 2550, 3300  # 8.5x11" at 300 DPI
    poster = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(poster)

    banner_h = 440
    d.rectangle([0, 0, W, banner_h], fill=CFA_RED)
    f_eyebrow = load_font("Medium", 60)
    f_title = load_font("Bold", 140)
    f_store = load_font("Medium", 66)
    f_body = load_font("Regular", 62)
    f_bullet = load_font("Regular", 58)
    f_card_title = load_font("Bold", 64)
    f_scan = load_font("Bold", 84)
    f_spots = load_font("Medium", 60)

    y = 80
    y = centred_text(d, y, "INVEST IN YOUR JOURNEY", f_eyebrow, WHITE, W) + 28
    centred_text(d, y, "LIFE PLANNING WORKSHOP", f_title, WHITE, W)

    logo = Image.open(LOGO_PATH).convert("RGBA")
    scale = 420 / logo.width
    logo_r = logo.resize((420, int(logo.height * scale)), Image.LANCZOS)
    poster.paste(logo_r, ((W - logo_r.width) // 2, banner_h + 55), logo_r)

    y = banner_h + 55 + logo_r.height + 30
    y = centred_text(d, y, "WHARNCLIFFE & WONDERLAND", f_store, CFA_RED, W) + 55

    for line in (
        "A 7 week guided journey through a vision and plan for the",
        "whole of your life: Career, Family, Friends, Community Impact,",
        "Faith, and Legacy. For anyone serious about their life's",
        "journey and maximizing growth in every area.",
    ):
        y = centred_text(d, y, line, f_body, INK, W) + 18

    # Commitment card: white rounded panel with the workshop expectations.
    card_x, card_w = 220, W - 440
    text_x = card_x + 90
    text_w = card_w - 180
    y += 45
    card_top = y
    cy = card_top + 45
    box = d.textbbox((0, 0), "THE COMMITMENT", font=f_card_title)
    title_h = box[3] - box[1]
    line_h = 72
    card_h = 45 + title_h + 36
    for item in COMMITMENT:
        card_h += len(wrap_text(d, item, f_bullet, text_w - 70)) * line_h + 20
    card_h += 22
    d.rounded_rectangle([card_x, card_top, card_x + card_w, card_top + card_h],
                        radius=40, fill=WHITE, outline=CFA_RED, width=8)
    d.text((text_x, cy), "THE COMMITMENT", font=f_card_title, fill=CFA_RED)
    cy += title_h + 36
    for item in COMMITMENT:
        d.ellipse([text_x + 6, cy + 18, text_x + 34, cy + 46], fill=CFA_RED)
        for line in wrap_text(d, item, f_bullet, text_w - 70):
            d.text((text_x + 70, cy), line, font=f_bullet, fill=INK)
            cy += line_h
        cy += 20
    y = card_top + card_h

    y = centred_text(d, y + 50, "Only 7 spots per group: 5 Team Members and 2 Leaders",
                     f_spots, CFA_RED, W)

    qr_size = 680
    qr_r = qr_img.resize((qr_size, qr_size), Image.LANCZOS)
    qr_card = Image.new("RGB", (qr_size + 70, qr_size + 70), WHITE)
    qr_card.paste(qr_r, (35, 35), qr_r)
    qy = y + 40
    poster.paste(qr_card, ((W - qr_card.width) // 2, qy))
    d.rounded_rectangle(
        [(W - qr_card.width) // 2, qy,
         (W + qr_card.width) // 2, qy + qr_card.height],
        radius=40, outline=CFA_RED, width=8)

    y = qy + qr_card.height + 40
    centred_text(d, y, "SCAN TO SIGN UP", f_scan, CFA_RED, W)

    foot_h = 150
    d.rectangle([0, H - foot_h, W, H], fill=CFA_RED)
    fb = load_font("Medium", 48)
    foot = "Chick-fil-A Wharncliffe & Wonderland · 3459 Wonderland Road South, London, ON"
    box = d.textbbox((0, 0), foot, font=fb)
    d.text(((W - (box[2] - box[0])) // 2,
            H - foot_h + (foot_h - (box[3] - box[1])) // 2),
           foot, font=fb, fill=WHITE)
    return poster


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    os.makedirs(OUT_DIR, exist_ok=True)
    qr_img = build_qr(url)
    qr_path = os.path.join(OUT_DIR, "life-planning-qr.png")
    qr_img.save(qr_path)
    poster = build_poster(qr_img)
    poster_path = os.path.join(OUT_DIR, "life-planning-poster.png")
    poster.save(poster_path, dpi=(300, 300))
    print(f"QR target: {url}")
    print(f"Wrote {qr_path}")
    print(f"Wrote {poster_path}")


if __name__ == "__main__":
    main()

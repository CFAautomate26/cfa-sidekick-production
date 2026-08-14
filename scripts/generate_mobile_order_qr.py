#!/usr/bin/env python3
"""Generate the Chick-fil-A App mobile-ordering QR codes and bag-stuffer cards.

Usage:
    python scripts/generate_mobile_order_qr.py [APPLE_URL] [ANDROID_URL]

URLs default to the Chick-fil-A Canada app's App Store and Google Play
pages. Scanning takes guests to the store to download the app; if the app
is already installed the store page shows an "Open" button instead, so
each QR serves both audiences on its platform. The card is double-sided:
the Apple QR on one side and the Android QR on the other, so a single
card works for every guest. Outputs in assets/mobile-order-qr/:
    mobile-order-qr-{p}.png        - branded QR code per platform
    mobile-order-card-side-{p}.png - 3.5x2" card side (300 DPI) per platform
    mobile-order-card.pdf          - card-size PDF (page 1 Apple side,
                                     page 2 Android side), print-shop ready
    mobile-order-card-sheet.pdf    - letter-size 10-up print sheet with
                                     crop marks (page 1 Apple sides, page 2
                                     Android sides; the layout is symmetric
                                     so long-edge duplex printing lines the
                                     two sides up)

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
DARK_RED = (175, 15, 40)
CREAM = (255, 250, 242)
INK = (60, 44, 33)
SOFT = (150, 130, 112)
WHITE = (255, 255, 255)

APPLE_URL = "https://apps.apple.com/app/id6673919737"
ANDROID_URL = ("https://play.google.com/store/apps/details"
               "?id=com.chickfila.international")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO_ROOT, "assets", "mobile-order-qr")
LOGO_PATH = os.environ.get(
    "CFA_LOGO", os.path.join(REPO_ROOT, "assets", "cfa-logo.png"))
APERCU_DIR = os.environ.get("APERCU_DIR", "")

# 3.5 x 2 inches at 300 DPI
CARD_W, CARD_H = 1050, 600


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


def text_w(draw, text, font):
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def build_card_front(qr_img: Image.Image, platform: str) -> Image.Image:
    card = Image.new("RGB", (CARD_W, CARD_H), CREAM)
    d = ImageDraw.Draw(card)

    # Right side: white QR panel with red outline, scan caption, and a
    # platform tag so the Apple and Android cards are easy to tell apart.
    panel_w, panel_h = 460, 520
    px = CARD_W - panel_w - 40
    py = (CARD_H - panel_h) // 2
    d.rounded_rectangle([px, py, px + panel_w, py + panel_h], radius=28,
                        fill=WHITE, outline=CFA_RED, width=6)
    qr_size = 400
    qr_r = qr_img.resize((qr_size, qr_size), Image.LANCZOS)
    card.paste(qr_r, (px + (panel_w - qr_size) // 2, py + 16), qr_r)
    f_scan = load_font("Bold", 42)
    scan = "SCAN TO ORDER"
    d.text((px + (panel_w - text_w(d, scan, f_scan)) // 2,
            py + 16 + qr_size + 6), scan, font=f_scan, fill=CFA_RED)
    f_tag = load_font("Regular", 24)
    tag = {"apple": "iPhone · On Android? Flip the card",
           "android": "Android · On iPhone? Flip the card"}[platform]
    d.text((px + (panel_w - text_w(d, tag, f_tag)) // 2, py + panel_h - 46),
           tag, font=f_tag, fill=SOFT)

    # Left side: logo, headline, app line, service modes, store footer.
    lx = 48
    logo = Image.open(LOGO_PATH).convert("RGBA")
    scale = 250 / logo.width
    logo_r = logo.resize((250, int(logo.height * scale)), Image.LANCZOS)
    card.paste(logo_r, (lx, 34), logo_r)

    y = 34 + logo_r.height + 18
    f_h1 = load_font("Bold", 62)
    d.text((lx, y), "SKIP", font=f_h1, fill=CFA_RED)
    y += 66
    d.text((lx, y), "THE LINE.", font=f_h1, fill=CFA_RED)
    y += 84

    f_sub = load_font("Medium", 30)
    d.text((lx, y), "Order ahead on the", font=f_sub, fill=INK)
    y += 38
    d.text((lx, y), "Chick-fil-A® App", font=f_sub, fill=INK)
    y += 54

    f_mode = load_font("Regular", 28)
    f_mode_b = load_font("Bold", 28)
    modes = [("Mobile Drive-Thru", True), ("Mobile Dine-In", False),
             ("Mobile Carry-Out", False)]
    for label, hot in modes:
        d.ellipse([lx, y + 9, lx + 12, y + 21], fill=CFA_RED)
        d.text((lx + 24, y), label, font=f_mode_b if hot else f_mode,
               fill=CFA_RED if hot else INK)
        y += 39

    f_foot = load_font("Regular", 21)
    d.text((lx, CARD_H - 44), "Wharncliffe & Wonderland · London, ON",
           font=f_foot, fill=SOFT)
    return card


def build_sheet(card: Image.Image) -> Image.Image:
    """Lay a card out 10-up (2 x 5) on a letter page with crop marks."""
    W, H = 2550, 3300  # 8.5x11" at 300 DPI
    gap = 30
    cols, rows = 2, 5
    x0 = (W - (cols * CARD_W + (cols - 1) * gap)) // 2
    y0 = (H - (rows * CARD_H + (rows - 1) * gap)) // 2
    sheet = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(sheet)
    mark, off = 22, 6
    for r in range(rows):
        for c in range(cols):
            x = x0 + c * (CARD_W + gap)
            y = y0 + r * (CARD_H + gap)
            sheet.paste(card, (x, y))
            for cx in (x, x + CARD_W):
                for cy in (y, y + CARD_H):
                    d.line([cx - mark - off, cy, cx - off, cy],
                           fill=(120, 120, 120), width=2)
                    d.line([cx + off, cy, cx + mark + off, cy],
                           fill=(120, 120, 120), width=2)
                    d.line([cx, cy - mark - off, cx, cy - off],
                           fill=(120, 120, 120), width=2)
                    d.line([cx, cy + off, cx, cy + mark + off],
                           fill=(120, 120, 120), width=2)
    return sheet


def main() -> None:
    apple_url = sys.argv[1] if len(sys.argv) > 1 else APPLE_URL
    android_url = sys.argv[2] if len(sys.argv) > 2 else ANDROID_URL
    os.makedirs(OUT_DIR, exist_ok=True)

    sides = {}
    for platform, url in (("apple", apple_url), ("android", android_url)):
        qr_img = build_qr(url)
        qr_path = os.path.join(OUT_DIR, f"mobile-order-qr-{platform}.png")
        qr_img.save(qr_path)

        side = build_card_front(qr_img, platform)
        side_path = os.path.join(
            OUT_DIR, f"mobile-order-card-side-{platform}.png")
        side.save(side_path, dpi=(300, 300))
        sides[platform] = side

        print(f"{platform} QR target: {url}")
        print(f"Wrote {qr_path}")
        print(f"Wrote {side_path}")

    card_pdf = os.path.join(OUT_DIR, "mobile-order-card.pdf")
    sides["apple"].save(card_pdf, resolution=300, save_all=True,
                        append_images=[sides["android"]])

    sheet_path = os.path.join(OUT_DIR, "mobile-order-card-sheet.pdf")
    build_sheet(sides["apple"]).save(
        sheet_path, resolution=300, save_all=True,
        append_images=[build_sheet(sides["android"])])

    for p in (card_pdf, sheet_path):
        print(f"Wrote {p}")


if __name__ == "__main__":
    main()

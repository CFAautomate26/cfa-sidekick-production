#!/usr/bin/env python3
"""
Holiday-hours social post generator for Chick-fil-A Wharncliffe & Wonderland.

Renders a feed (1080x1080) and story (1080x1920) graphic announcing special
holiday hours across all sales channels. Solo-store variant of the co-branded
event flyer: same palette, Apercu type, and store lockup, no partner logo.

Brand assets (Apercu OTFs + the store lockup PNG) are read from the
cfa-event-flyer skill when present, else from an assets dir passed via
CFA_BRAND_ASSETS. Falls back to system fonts / a text wordmark so it still
runs anywhere.

Usage:
    python generate_holiday_hours_post.py [out_dir]
"""
import glob
import os
import sys

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- brand assets
def _find_assets():
    cands = [os.environ.get("CFA_BRAND_ASSETS")]
    cands += sorted(glob.glob(os.path.expanduser(
        "~/.claude/skills/synced/*/cfa-event-flyer/assets")))
    for c in cands:
        if c and os.path.isdir(c):
            return c
    return None

ASSETS = _find_assets()

CFA_RED = (227, 25, 55)
CREAM = (255, 250, 242)
INK = (60, 44, 33)
WHITE = (255, 255, 255)
SOFT = (150, 130, 112)
PALE_RED = (255, 219, 224)
RULE = (214, 200, 188)
SS = 3  # supersample factor

def _font_path(*names):
    cands = []
    if ASSETS:
        cands += [os.path.join(ASSETS, "fonts", n) for n in names]
    cands += [
        "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return cands[-1]

APERCU_BOLD = _font_path("Apercu-Bold.otf")
APERCU_MED = _font_path("Apercu-Medium.otf", "Apercu-Bold.otf")
LOCKUP = os.path.join(ASSETS, "logos", "cfa-wharncliffe-wonderland-lockup.png") if ASSETS else None

def F(path, size):
    return ImageFont.truetype(path, int(size))

# ---------------------------------------------------------------- copy
POST = {
    "banner_text": "LABOUR DAY",
    "eyebrow": "HOLIDAY HOURS AT",
    "date_line": "MONDAY, SEPTEMBER 7",
    "hours_line": "OPEN 10:30 AM TO 4 PM",
    "channels_label": "ALL WAYS TO ORDER",
    "channel_lines": ["DINE IN  ·  CARRYOUT  ·  DRIVE-THRU", "THIRD PARTY DELIVERY"],
    "cta": "Happy Labour Day! We'll see you Monday.",
    "address": "3459 Wonderland Road South, London, ON",
    "footnote": "Regular hours resume Tuesday",
    "name_prefix": "CFA-Labour-Day-Hours",
}
SIZES = {"square": (1080, 1080, 132, 126), "story": (1080, 1920, 150, 150)}

# ---------------------------------------------------------------- helpers
def tlen(draw, text, font, track=0):
    w = draw.textlength(text, font=font)
    if track and len(text) > 1:
        w += track * (len(text) - 1)
    return w

def draw_tracked(draw, x, y, text, font, fill, track=0):
    total = tlen(draw, text, font, track)
    cx = x - total / 2
    for ch in text:
        draw.text((cx, y), ch, font=font, fill=fill, anchor="ls")
        cx += draw.textlength(ch, font=font) + track
    return total

def cap_h(font):
    b = font.getbbox("HJALI")
    return b[3] - b[1]

def load_logo(path):
    logo = Image.open(path).convert("RGBA")
    bb = logo.getbbox()
    return logo.crop(bb) if bb else logo

def paste(img, logo, cx, top_y, target_w):
    h = max(1, int(round(target_w * logo.height / logo.width)))
    lg = logo.resize((int(round(target_w)), h), Image.LANCZOS)
    img.paste(lg, (int(round(cx - target_w / 2)), int(round(top_y))), lg)
    return h

def star(draw, cx, cy, r, fill):
    import math
    pts = []
    for i in range(8):
        ang = math.pi / 2 + i * math.pi / 4
        rad = r if i % 2 == 0 else r * 0.4
        pts.append((cx + rad * math.cos(ang), cy - rad * math.sin(ang)))
    draw.polygon(pts, fill=fill)

# ---------------------------------------------------------------- render
def render(cfg, fmt, out_dir):
    W, H, tb, bb = SIZES[fmt]
    s = SS
    W *= s; H *= s; tb *= s; bb *= s
    img = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(img)
    cx = W // 2

    # top red banner
    d.rectangle([0, 0, W, tb], fill=CFA_RED)
    bf = F(APERCU_BOLD, tb * 0.38)
    bcap = cap_h(bf)
    tw = draw_tracked(d, cx, tb / 2 + bcap / 2, cfg["banner_text"], bf, WHITE, track=tb * 0.045)
    sr = tb * 0.13
    star(d, cx - tw / 2 - sr * 2.2, tb / 2, sr, WHITE)
    star(d, cx + tw / 2 + sr * 2.2, tb / 2, sr, WHITE)

    # bottom red bar
    d.rectangle([0, H - bb, W, H], fill=CFA_RED)
    n1 = F(APERCU_BOLD, bb * 0.250)
    n2 = F(APERCU_MED, bb * 0.205)
    d.text((cx, H - bb + bb * 0.37), cfg["address"], font=n1, fill=WHITE, anchor="mm")
    d.text((cx, H - bb + bb * 0.72), cfg["footnote"], font=n2, fill=PALE_RED, anchor="mm")

    blocks = []  # (height, draw_fn(top_y))

    # eyebrow with flanking rules
    ebf = F(APERCU_MED, W * 0.030)
    eb_h = cap_h(ebf)
    def b_eyebrow(y):
        tw2 = tlen(d, cfg["eyebrow"], ebf, W * 0.030 * 0.18)
        draw_tracked(d, cx, y + eb_h, cfg["eyebrow"], ebf, SOFT, track=W * 0.030 * 0.18)
        ly = y + eb_h * 0.55
        lw = W * 0.14
        gap = tw2 / 2 + W * 0.03
        d.line([cx - gap - lw, ly, cx - gap, ly], fill=RULE, width=int(2 * s))
        d.line([cx + gap, ly, cx + gap + lw, ly], fill=RULE, width=int(2 * s))
    blocks.append((eb_h, b_eyebrow))

    # store lockup, the hero of this layout
    if LOCKUP and os.path.exists(LOCKUP):
        lock = load_logo(LOCKUP)
        lw_ = W * 0.56
        lh = int(round(lw_ * lock.height / lock.width))
        blocks.append((lh, lambda y: paste(img, lock, cx, y, lw_)))
    else:
        wf = F(APERCU_BOLD, W * 0.085)
        wcap = cap_h(wf)
        sf = F(APERCU_BOLD, W * 0.030)
        scap = cap_h(sf)
        wh = wcap + W * 0.028 + scap
        def b_word(y):
            d.text((cx, y + wcap), "Chick-fil-A", font=wf, fill=CFA_RED, anchor="ms")
            draw_tracked(d, cx, y + wcap + W * 0.028 + scap, "WHARNCLIFFE & WONDERLAND",
                         sf, CFA_RED, track=W * 0.030 * 0.14)
        blocks.append((wh, b_word))

    # date
    dtf = F(APERCU_BOLD, W * 0.056)
    dcap = cap_h(dtf)
    blocks.append((dcap, lambda y: draw_tracked(
        d, cx, y + dcap, cfg["date_line"], dtf, INK, track=W * 0.056 * 0.02)))

    # hours pill, single big line
    pf = F(APERCU_BOLD, W * 0.044)
    pcap = cap_h(pf)
    pill_h = pcap + W * 0.062
    pill_w = tlen(d, cfg["hours_line"], pf, W * 0.044 * 0.04) + W * 0.10
    def b_pill(y):
        x0 = cx - pill_w / 2
        d.rounded_rectangle([x0, y, x0 + pill_w, y + pill_h], radius=pill_h * 0.5, fill=CFA_RED)
        draw_tracked(d, cx, y + pill_h / 2 + pcap / 2, cfg["hours_line"], pf, WHITE,
                     track=W * 0.044 * 0.04)
    blocks.append((pill_h, b_pill))

    # channels: label + lines
    clf = F(APERCU_MED, W * 0.024)
    clcap = cap_h(clf)
    chf = F(APERCU_BOLD, W * 0.031)
    chcap = cap_h(chf)
    line_gap = chcap * 0.85
    ch_h = clcap + chcap * 0.9 + len(cfg["channel_lines"]) * (chcap + line_gap) - line_gap
    def b_channels(y):
        draw_tracked(d, cx, y + clcap, cfg["channels_label"], clf, SOFT, track=W * 0.024 * 0.22)
        yy = y + clcap + chcap * 0.9
        for ln in cfg["channel_lines"]:
            draw_tracked(d, cx, yy + chcap, ln, chf, INK, track=W * 0.031 * 0.05)
            yy += chcap + line_gap
    blocks.append((ch_h, b_channels))

    # CTA
    ctaf = F(APERCU_MED, W * 0.027)
    cta_h = cap_h(ctaf)
    blocks.append((cta_h, lambda y: d.text(
        (cx, y + cta_h), cfg["cta"], font=ctaf, fill=SOFT, anchor="ms")))

    # distribute vertically between the bars
    avail_top = tb + H * 0.045
    avail_bot = H - bb - H * 0.03
    avail = avail_bot - avail_top
    sum_h = sum(b[0] for b in blocks)
    n_gaps = len(blocks) - 1
    gap = min(H * 0.062, (avail - sum_h) / max(1, n_gaps))
    y = avail_top + (avail - (sum_h + gap * n_gaps)) / 2
    for h, fn in blocks:
        fn(y)
        y += h + gap

    out = img.resize((W // s, H // s), Image.LANCZOS)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{cfg['name_prefix']}-{fmt}-{out.width}x{out.height}.png")
    out.save(path, "PNG")
    return path

def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "./assets/labour-day-2026"
    for fmt in SIZES:
        print("saved", render(POST, fmt, out_dir))
    print("done")

if __name__ == "__main__":
    main()

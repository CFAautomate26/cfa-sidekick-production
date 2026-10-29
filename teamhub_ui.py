"""Team Hub's drawn pieces: line icons (24-grid), two-tone section art
(72-grid, navy + red) and the checklist progress ring. All inline SVG, so
pages need no image requests and no JavaScript (the Hub runs none).
Registered as template globals by register(app)."""

import math

from markupsafe import Markup

ICONS = {
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "close": '<path d="M5 5l14 14M19 5L5 19"/>',
    "chev": '<polyline points="9 5.5 15.5 12 9 18.5"/>',
    "back": '<polyline points="15 5.5 8.5 12 15 18.5"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
    "home": '<path d="M3.5 11 12 4l8.5 7"/><path d="M6 9.5V20h4.5v-5.5h3V20H18V9.5"/>',
    "clip": '<rect x="5" y="4.5" width="14" height="16.5" rx="2"/><path d="M9 4.5V3h6v1.5"/><path d="m8.5 12.5 2.2 2.2 4.6-4.7"/>',
    "users": '<circle cx="9" cy="8" r="3.3"/><path d="M3 20c0-3.6 2.7-6 6-6s6 2.4 6 6"/><circle cx="17" cy="9" r="2.4"/><path d="M17 13.6c2.6 0 4.2 1.9 4.2 4.6"/>',
    "book": '<path d="M12 6.5C10 5 7 4.6 3 5v13c4-.4 7 .1 9 1.5 2-1.4 5-1.9 9-1.5V5c-4-.4-7 0-9 1.5z"/><path d="M12 6.5v13"/>',
    "chat": '<path d="M4 4.5h11a1 1 0 0 1 1 1V13a1 1 0 0 1-1 1H9l-4 3.2V14H4a1 1 0 0 1-1-1V5.5a1 1 0 0 1 1-1z"/><path d="M19 9h1a1 1 0 0 1 1 1v6.5a1 1 0 0 1-1 1h-1v3l-3.6-3H12a1 1 0 0 1-1-1V17"/>',
    "target": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.8"/><circle cx="12" cy="12" r="1.3"/>',
    "cap": '<path d="M2.5 9.5 12 5l9.5 4.5L12 14z"/><path d="M6.5 11.6v4.6c2.6 2.4 8.4 2.4 11 0v-4.6"/><path d="M21.5 9.5v5.5"/>',
    "user": '<circle cx="12" cy="8.2" r="3.8"/><path d="M4.5 20.5c0-4.1 3.4-6.6 7.5-6.6s7.5 2.5 7.5 6.6"/>',
    "bookmark": '<path d="M6.5 3.5h11v17L12 16.4l-5.5 4.1z"/>',
    "pin": '<path d="M12 21s-6.5-6.1-6.5-11.3a6.5 6.5 0 0 1 13 0C18.5 14.9 12 21 12 21z"/><circle cx="12" cy="9.6" r="2.3"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8.2 10.5V7.8a3.8 3.8 0 0 1 7.6 0v2.7"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "star": '<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
    "mega": '<path d="M3.5 10v4a1 1 0 0 0 1 1h2.5l8 5V4l-8 5H4.5a1 1 0 0 0-1 1z"/><path d="M18.5 9a4.2 4.2 0 0 1 0 6"/>',
    "heart": '<path d="M12 20s-7.5-4.6-7.5-10.1A4.3 4.3 0 0 1 12 7.3a4.3 4.3 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20z"/>',
    "note": '<path d="M5.5 3.5h9l4 4v13h-13z"/><path d="M14.5 3.5v4h4"/><path d="M8.5 12h7M8.5 16h5"/>',
    "history": '<path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1"/><path d="M3.5 4v4.2h4.2"/><path d="M12 8v4.2l2.8 1.8"/>',
    "sliders": '<path d="M4 6.5h9M17 6.5h3M4 12h3M11 12h9M4 17.5h11M19 17.5h1"/><circle cx="15" cy="6.5" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="17.5" r="2"/>',
    "check": '<polyline points="5 12.5 10 17 19 7.5"/>',
    "circle": '<circle cx="12" cy="12" r="8.5"/>',
    "checkcircle": '<circle cx="12" cy="12" r="8.5"/><polyline points="8 12.3 10.8 15 16 9.5"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13 7l4 4"/>',
    "signout": '<path d="M14.5 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4"/><path d="M9.5 8 5.5 12l4 4M5.5 12h10"/>',
    "file": '<path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4"/>',
    "camera": '<path d="M4 8h3l1.6-2.5h6.8L17 8h3v11H4z"/><circle cx="12" cy="13" r="3.4"/>',
    "alert": '<path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4.2M12 17h.01"/>',
    "info": '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/>',
    "list": '<path d="M9 6.5h11M9 12h11M9 17.5h11"/><path d="M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01"/>',
    "share": '<path d="M12 3.5v11M8 7.5l4-4 4 4"/><path d="M6 11v9h12v-9"/>',
}
ICONS.update({
    "zap": '<path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/>',
    "door": '<path d="M5 20.5h14"/><path d="M7 20.5V4.5h10v16"/><path d="M14 12.5h.01"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M6 6l1.6 1.6M16.4 16.4 18 18M6 18l1.6-1.6M16.4 7.6 18 6"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M10.8 12.2 19 4M16 7l2.5 2.5M14 9l2 2"/>',
    "folder": '<path d="M3.5 6.5a1 1 0 0 1 1-1h5l2 2.5h8a1 1 0 0 1 1 1V18a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1z"/>',
    "upload": '<path d="M12 15V4M8 8l4-4 4 4"/><path d="M4.5 15v4.5h15V15"/>',
    "download": '<path d="M12 4v11M8 11l4 4 4-4"/><path d="M4.5 15v4.5h15V15"/>',
    "rocket": '<path d="M12 15.5 8.5 12C10 7 13.5 4 19.5 4.5 20 10.5 17 14 12 15.5z"/><path d="M8.5 12 5 11.5l2.5-3.5h4M12 15.5l.5 3.5 3.5-2.5v-4"/><circle cx="15" cy="9" r="1.3"/>',
    "shield": '<path d="M12 3.5 5 6v5.5c0 4.6 3 7.8 7 9 4-1.2 7-4.4 7-9V6z"/>',
    "phone": '<rect x="7" y="3" width="10" height="18" rx="2"/><path d="M11 17.5h2"/>',
})

N, R, W, T = "#1F4F6E", "#DD0031", "#FFFFFF", "#DBC8B0"
ART = {
    "procedures": f'<path d="M36 19c-7.5-5-17.5-6.5-29-5.5v40c11.5-1 21.5.5 29 5.5 7.5-5 17.5-6.5 29-5.5v-40c-11.5-1-21.5.5-29 5.5z" fill="{W}" stroke="{N}" stroke-width="3" stroke-linejoin="round"/>'
                  f'<path d="M36 19v40" stroke="{N}" stroke-width="3"/>'
                  f'<path d="M14.5 26.5c4.5 0 9 .7 13.5 2.2M14.5 34.5c4.5 0 9 .7 13.5 2.2M14.5 42.5c3 0 6 .4 9 1.1" stroke="{N}" stroke-width="2.6" stroke-linecap="round" fill="none"/>'
                  f'<path d="M46 12.5h11V33l-5.5-4-5.5 4z" fill="{R}"/>',
    "hr": f'<rect x="15" y="8" width="38" height="50" rx="5" fill="{W}" stroke="{N}" stroke-width="3"/>'
          f'<path d="M23 20h22M23 28h22M23 36h13" stroke="{N}" stroke-width="3" stroke-linecap="round"/>'
          f'<circle cx="50" cy="52" r="12" fill="{R}"/>'
          f'<path d="M44.5 52l4 4 7-7.5" fill="none" stroke="{W}" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>',
    "foh": f'<path d="M39 16l6.5-10.5" stroke="{N}" stroke-width="3" stroke-linecap="round"/>'
           f'<path d="M22 23h28l-3.6 41H25.6z" fill="{R}" stroke="{N}" stroke-width="3" stroke-linejoin="round"/>'
           f'<path d="M24.4 37h23.2" stroke="{W}" stroke-width="4"/>'
           f'<rect x="18.5" y="15.5" width="35" height="8" rx="3.5" fill="{W}" stroke="{N}" stroke-width="3"/>',
    "boh": f'<path d="M22 40c-6.5-1-10-6-9-11.5 1.2-6 7-9 12.5-7.5C27 14 32 10 37 10s10 4 11.5 11c5.5-1.5 11.3 1.5 12.5 7.5 1 5.5-2.5 10.5-9 11.5" fill="{W}" stroke="{N}" stroke-width="3" stroke-linejoin="round"/>'
           f'<path d="M22 40v16a4 4 0 0 0 4 4h22a4 4 0 0 0 4-4V40z" fill="{W}" stroke="{N}" stroke-width="3" stroke-linejoin="round"/>'
           f'<rect x="23.5" y="45" width="27" height="7" fill="{R}"/>',
    "ld": f'<path d="M18 33v13c0 5 8 9.5 18 9.5s18-4.5 18-9.5V33l-18 8z" fill="{W}" stroke="{N}" stroke-width="3" stroke-linejoin="round"/>'
          f'<path d="M36 13 6 27l30 14 30-14z" fill="{N}" stroke="{N}" stroke-width="3" stroke-linejoin="round"/>'
          f'<path d="M58 31v15" stroke="{R}" stroke-width="3" stroke-linecap="round"/><circle cx="58" cy="49" r="4.5" fill="{R}"/>',
    "checklists": f'<rect x="15" y="12" width="42" height="52" rx="6" fill="{W}" stroke="{N}" stroke-width="3"/>'
                  f'<rect x="26" y="7" width="20" height="10" rx="3" fill="{N}"/>'
                  f'<path d="M23 31l4 4 7.5-8M23 48l4 4 7.5-8" fill="none" stroke="{R}" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round"/>'
                  f'<path d="M40 32h10M40 49h10" stroke="{N}" stroke-width="3" stroke-linecap="round"/>',
    "meetings": f'<path d="M10 12h32a5 5 0 0 1 5 5v18a5 5 0 0 1-5 5H24l-9 8v-8h-5a5 5 0 0 1-5-5V17a5 5 0 0 1 5-5z" fill="{W}" stroke="{N}" stroke-width="3" stroke-linejoin="round"/>'
                f'<path d="M30 30h29a5 5 0 0 1 5 5v15a5 5 0 0 1-5 5h-3v9l-9.5-9H30a5 5 0 0 1-5-5V35a5 5 0 0 1 5-5z" fill="{R}"/>'
                f'<circle cx="36.5" cy="42.5" r="2.6" fill="{W}"/><circle cx="44.5" cy="42.5" r="2.6" fill="{W}"/><circle cx="52.5" cy="42.5" r="2.6" fill="{W}"/>',
}
N_, R_, W_ = N, R, W
ART.update({
    "search": f'<circle cx="31" cy="31" r="18" fill="{W_}" stroke="{N_}" stroke-width="3"/>'
              f'<path d="M44 44l14 14" stroke="{R_}" stroke-width="7" stroke-linecap="round"/>'
              f'<path d="M22 27c2-4 5.5-6 9.5-6" stroke="{N_}" stroke-width="3" stroke-linecap="round" fill="none"/>',
    "today": f'<rect x="10" y="14" width="52" height="46" rx="6" fill="{W_}" stroke="{N_}" stroke-width="3"/>'
             f'<path d="M10 26h52" stroke="{N_}" stroke-width="3"/><path d="M23 9v10M49 9v10" stroke="{N_}" stroke-width="3" stroke-linecap="round"/>'
             f'<circle cx="36" cy="43" r="9" fill="{R_}"/>',
    # Guest recovery: a heart inside a coming-back arrow (make it right,
    # win the guest back).
    "recovery": f'<path d="M57.5 30A23 23 0 1 0 59 40" fill="none" stroke="{N_}" stroke-width="3.2" stroke-linecap="round"/>'
                f'<path d="M50 29.5l8 .8 1.6-8" fill="none" stroke="{N_}" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>'
                f'<path d="M36 46.5s-14-8.3-14-17.9a7.7 7.7 0 0 1 14-4.8 7.7 7.7 0 0 1 14 4.8c0 9.6-14 17.9-14 17.9z" fill="{R_}"/>',
    # To-dos: a list of tick circles, the first one done (as the lists look).
    "todos": f'<circle cx="17" cy="18" r="8" fill="{R_}"/><path d="M13 18.3l3 3 5.4-5.6" fill="none" stroke="{W_}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
             f'<path d="M32 18h30" stroke="{N_}" stroke-width="3.4" stroke-linecap="round"/>'
             f'<circle cx="17" cy="37" r="7.5" fill="{W_}" stroke="{N_}" stroke-width="3"/><path d="M32 37h30" stroke="{N_}" stroke-width="3.4" stroke-linecap="round"/>'
             f'<circle cx="17" cy="56" r="7.5" fill="{W_}" stroke="{N_}" stroke-width="3"/><path d="M32 56h20" stroke="{N_}" stroke-width="3.4" stroke-linecap="round"/>',
    # Equipment & repairs: a wrench over a gear.
    "equipment": f'<path d="M42.9 6.1L47.1 6.1L49.6 11.7L52.5 12.9L58.3 10.8L61.2 13.7L59.1 19.5L60.3 22.4L65.9 24.9L65.9 29.1L60.3 31.6L59.1 34.5L61.2 40.3L58.3 43.2L52.5 41.1L49.6 42.3L47.1 47.9L42.9 47.9L40.4 42.3L37.5 41.1L31.7 43.2L28.8 40.3L30.9 34.5L29.7 31.6L24.1 29.1L24.1 24.9L29.7 22.4L30.9 19.5L28.8 13.7L31.7 10.8L37.5 12.9L40.4 11.7z" fill="{R_}" stroke="{R_}" stroke-width="1.5" stroke-linejoin="round"/>'
                 f'<circle cx="45" cy="27" r="7" fill="{W_}"/>'
                 f'<path d="M48 16a13.3 13.3 0 0 0-16.4 16.4L9.3 54.7a5.2 5.2 0 0 0 7.4 7.4l22.2-22.2a13.3 13.3 0 0 0 16.4-16.4l-7.6 7.6-8-2.2-2.2-8z" fill="{W_}" stroke="{N_}" stroke-width="3" stroke-linejoin="round"/>',
})


def icon(name: str, cls: str = "") -> Markup:
    c = f"i {cls}".strip()
    return Markup(f'<svg class="{c}" viewBox="0 0 24 24" aria-hidden="true">{ICONS[name]}</svg>')


def art(name: str) -> Markup:
    return Markup(f'<svg viewBox="0 0 72 72" aria-hidden="true">{ART[name]}</svg>')


def ring(left: int, total: int, critical: bool = False) -> Markup:
    """Items LEFT in the centre: a red arc while critical items are left, a
    navy arc in progress, a solid green disc with a tick when done."""
    left, total = max(int(left), 0), max(int(total), 0)
    if total and left == 0:
        return Markup('<svg class="ring" viewBox="0 0 44 44" role="img" aria-label="Done">'
                      '<circle cx="22" cy="22" r="21" fill="#00635B"/>'
                      '<polyline points="13.5 22.5 19.5 28.5 31 16" fill="none" stroke="#fff" '
                      'stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/></svg>')
    r, c = 19, 2 * math.pi * 19
    arc = c * (total - left) / total if total else 0
    color = "#DD0031" if critical else "#004F71"
    num_color = "#AF272F" if critical else "#3B4449"
    return Markup(
        f'<svg class="ring" viewBox="0 0 44 44" role="img" aria-label="{left} left of {total}">'
        f'<circle cx="22" cy="22" r="{r}" fill="#fff" stroke="#F0F1F2" stroke-width="4"/>'
        f'<circle cx="22" cy="22" r="{r}" fill="none" stroke="{color}" stroke-width="4" '
        f'stroke-linecap="round" stroke-dasharray="{arc:.1f} {c:.1f}" transform="rotate(-90 22 22)"/>'
        f'<text class="n" x="22" y="23.5" text-anchor="middle" fill="{num_color}">{left}</text>'
        f'<text class="l" x="22" y="32.5" text-anchor="middle" fill="#67737C">LEFT</text></svg>')


def register(app) -> None:
    import teamhub_nav
    app.jinja_env.globals.update(icon=icon, art=art, ring=ring,
                                 login_url=teamhub_nav.login_url, hub_up=teamhub_nav.hub_up)

"""Team Hub navigation: one place decides what each person's tabs, menu
and home-screen manifest look like, for both blueprints (/shift — the
leader tools — and /hub — Systems & Procedures, open to team members).

Navigation only hides links. The routes stay the gate: /shift's
before_request rejects anyone who isn't a leader, and hub_db.can_view
filters every Hub page. A link here must never point somewhere its viewer
can't open — tests walk every role's menu to check.
"""

from flask import current_app, request, url_for

import shift_db

# A leader's home is their to-dos (Today), then Checklists and the things
# the team uses most: Systems & Procedures, 1:1 meetings and Leadership
# Development. (key, icon, label, endpoint) — labels are a single word.
LEADER_TABS = [
    ("today", "checkcircle", "To-dos", "shift.today"),
    ("checklists", "clip", "Checklists", "shift.checklists"),
    ("procedures", "book", "Procedures", "hub.library"),
    ("meetings", "chat", "Meetings", "shift.oneonone"),
    ("development", "cap", "Leadership", "shift.development"),
]
MEMBER_TABS = [
    ("home", "home", "Home", "hub.home"),
    ("procedures", "book", "Procedures", "hub.library"),
    ("toread", "bookmark", "To read", "hub.to_read"),
    ("me", "user", "Me", "hub.me"),
]

# Which section an endpoint belongs to (the red tab and the red menu word).
_SECTION_OF = {
    "shift.today": "today",
    "shift.checklists": "checklists", "shift.checklist_scorecard": "checklists",
    "shift.run_detail": "checklists", "shift.history": "checklists",
    "shift.admin_templates": "checklists", "shift.admin_template_new": "checklists",
    "shift.admin_template_edit": "checklists",
    "shift.oneonone": "meetings", "shift.oneonone_leader": "meetings",
    "shift.oneonone_member": "meetings",
    "shift.todo": "today", "shift.todo_leader": "today",   # Today is the to-dos
    "shift.development": "development", "shift.development_leader": "development",
    "hub.home": "home", "hub.to_read": "toread", "hub.me": "me",
}
_HUB_PROCEDURES = {"hub.library", "hub.category", "hub.page", "hub.search", "hub.new_page",
                   "hub.edit_page", "hub.history", "hub.old_version", "hub.readers",
                   "hub.manage", "hub.people", "hub.remove_file"}


def hub_up() -> bool:
    """The Team Hub blueprint registers separately, and can fail to load
    without taking the leader tools down: links into it check first."""
    return "hub" in current_app.blueprints


def login_url() -> str:
    """The front door (the Hub's), or the leader sign-in if the Hub is down."""
    return url_for("hub.login") if hub_up() else url_for("shift.login")


def section_for(endpoint: str | None, kind: str) -> str:
    endpoint = endpoint or ""
    if endpoint in _HUB_PROCEDURES:
        return "procedures"
    section = _SECTION_OF.get(endpoint, "")
    if kind != "member" and section == "toread":
        return "procedures"
    return section


def manifest() -> dict:
    """The one Team Hub web-app manifest, served at /hub/ and /shift/.
    scope "/" keeps a home-screen install in standalone mode across both."""
    return {
        "name": "Team Hub",
        "short_name": "Team Hub",
        "start_url": "/hub/" if hub_up() else "/shift/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#FFFFFF",
        "theme_color": "#FFFFFF",
        "icons": [
            {"src": "/static/teamhub/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/teamhub/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }


def _url(endpoint: str, **kw) -> str:
    return url_for(endpoint, **kw)


def nav_for(kind: str, name: str, role: str = "", counts: dict | None = None,
            categories: list | None = None) -> dict:
    """Everything the chrome needs for one viewer.

    kind: member | leader | operator; role: a leader's lead | admin.
    counts: unread, recoveries, coming_back, repairs_to_call, repairs_waiting,
    meetings, todos, to_read, drafts (missing = 0).
    categories: the Hub categories a member may see (their menu words).
    """
    counts = counts or {}
    up = hub_up()
    member = kind == "member"
    admin = kind == "operator" or role == "admin"
    current = section_for(request.endpoint, kind)

    def tab(key, icon, label, endpoint, count=0):
        return {"key": key, "icon": icon, "label": label, "url": _url(endpoint),
                "count": count, "on": key == current}

    if member:
        tabs = [tab(*t, count=counts.get("to_read", 0) if t[0] == "toread" else 0)
                for t in MEMBER_TABS]
    else:
        tab_counts = {"today": counts.get("todos", 0), "meetings": counts.get("meetings", 0)}
        tabs = [tab(*t, count=tab_counts.get(t[0], 0))
                for t in LEADER_TABS if up or t[3] != "hub.library"]

    big: list[dict] = []
    small: list[dict] = []
    if member:
        big.append({"label": "Home", "url": _url("hub.home"), "on": current == "home"})
        big.append({"caption": "Systems & Procedures"})
        for c in categories or []:
            big.append({"label": c["name"], "url": _url("hub.category", category_id=c["id"]),
                        "on": False})
        big.append({"label": "To read", "url": _url("hub.to_read"),
                    "count": counts.get("to_read", 0), "on": current == "toread"})
        small += [
            {"icon": "search", "label": "Search all pages", "url": _url("hub.search")},
            {"icon": "rocket", "label": "Want to lead one day? Apply", "url": "/apply"},
            {"icon": "key", "label": "Shift leader sign-in",
             "url": _url("hub.login", **{"as": "leader"})},
        ]
    else:
        def word(label, key, endpoint, count=0):
            return {"label": label, "url": _url(endpoint), "count": count, "on": key == current}
        big += [word("To-dos", "today", "shift.today", counts.get("todos", 0)),
                word("Checklists", "checklists", "shift.checklists")]
        if up:
            big.append(word("Systems & Procedures", "procedures", "hub.library"))
        big += [word("Meetings", "meetings", "shift.oneonone", counts.get("meetings", 0)),
                word("Leadership Development", "development", "shift.development")]
        recoveries, unread = counts.get("recoveries", 0), counts.get("unread", 0)
        coming_back = counts.get("coming_back", 0)
        to_call, awaiting = counts.get("repairs_to_call", 0), counts.get("repairs_waiting", 0)
        small += [
            {"icon": "note", "label": "Shift notes", "url": _url("shift.notes")},
            # Red only while a guest is still waiting to be reached; once
            # they're contacted it's a calm note, matching the page.
            {"icon": "heart", "label": "Guest recovery", "url": _url("shift.recovery"),
             "badge": (f"{recoveries} open" if recoveries
                       else f"{coming_back} coming back" if coming_back else ""),
             "tone": "b-red" if recoveries else "b-tan"},
            # Same rule: red while nobody has called for service yet.
            {"icon": "gear", "label": "Equipment & repairs", "url": _url("shift.equipment"),
             "badge": (f"{to_call} to call in" if to_call
                       else f"{awaiting} awaiting repair" if awaiting else ""),
             "tone": "b-red" if to_call else "b-tan"},
            {"icon": "mega", "label": "Announcements", "url": _url("shift.announcements"),
             "badge": f"{unread} new" if unread else "", "tone": "b-red"},
            {"icon": "star", "label": "Shout-outs", "url": _url("shift.shoutouts")},
            {"icon": "users", "label": "Team roster", "url": _url("shift.roster")},
            {"icon": "history", "label": "History", "url": _url("shift.history")},
        ]
        if admin:
            small.append({"caption": "Admin"})
            drafts = counts.get("drafts", 0)
            if up:
                small.append({"icon": "edit", "label": "Manage pages", "url": _url("hub.manage"),
                              "badge": f"{drafts} draft{'s' * (drafts != 1)}" if drafts else "",
                              "tone": "b-tan"})
            small.append({"icon": "sliders", "label": "Leader logins & checklist templates",
                          "url": _url("shift.admin")})
        if kind == "operator":
            small.append({"icon": "download", "label": "Backups",
                          "url": _url("shift.admin") + "#backups"})
            if up:
                small += [
                    {"icon": "key", "label": "Team Hub access (clock-in numbers)",
                     "url": _url("hub.people")},
                    {"icon": "folder", "label": "Categories",
                     "url": _url("hub.manage") + "#categories"},
                ]

    role_label = ("Team member" if member else "Operator" if kind == "operator"
                  else "Shift leader · admin" if admin else "Shift leader")
    first = (name or "").split()[0] if name else ""
    return {
        "kind": kind,
        "member": member,
        "admin": admin,
        "name": name,
        "first": first if member else name,
        "initial": (name or "?")[:1].upper(),
        "role_label": role_label,
        "home": _url("hub.home") if member else _url("shift.today"),
        "menu": (_url("hub.menu") if member or (up and request.blueprint == "hub")
                 else _url("shift.more")),
        "search": _url("hub.search") if up else "",
        "me": _url("hub.me") if up else "",
        "logout": _url("hub.logout") if member else _url("shift.logout"),
        "current": current,
        "tabs": tabs,
        "big": big,
        "small": small,
    }


def _meetings_count(kind: str, leader_id: int | None) -> int:
    """Open 1:1 topics: a leader's own agenda + their team threads; the
    Operator's, every leader's open agenda."""
    if kind == "operator":
        return shift_db.open_topic_total()
    return (shift_db.open_topic_count(leader_id)
            + shift_db.open_member_topic_count(leader_id))


def _hub_drafts() -> int:
    if not hub_up():
        return 0
    try:
        import hub_db
        return hub_db.draft_count()
    except Exception:           # the Hub's trouble must never break /shift
        return 0


def leader_nav(kind: str, name: str, role: str, leader_id: int | None) -> dict:
    """Navigation for a signed-in leader or the Operator."""
    open_recs, coming_back, _ = shift_db.recovery_feed(resolved_limit=0)
    to_call, awaiting = shift_db.equipment_counts()
    counts = {"unread": shift_db.unacked_count(name),
              "recoveries": len(open_recs), "coming_back": len(coming_back),
              "repairs_to_call": to_call, "repairs_waiting": awaiting,
              "meetings": _meetings_count(kind, leader_id),
              # What each home lists: a leader's own open to-dos; the
              # Operator's, the open ones they handed out.
              "todos": (shift_db.open_task_counts(assigned_by=name)
                        if kind == "operator" else
                        shift_db.open_task_counts(leader_id))[0]}
    if kind == "operator" or role == "admin":
        counts["drafts"] = _hub_drafts()
    return nav_for(kind, name, role, counts)


def member_nav(viewer) -> dict:
    """Navigation for a team member signed in with their clock-in number."""
    import hub_db
    return nav_for("member", viewer.name,
                   counts={"to_read": len(hub_db.to_read(viewer))},
                   categories=hub_db.categories(viewer))

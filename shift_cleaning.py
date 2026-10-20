"""The store's posted station cleaning sheets, pinned as checklist templates.

Source: the laminated sheets on the wall — "Dining Room & Washrooms" and
"Drive Thru" (Chick-fil-A Wharncliffe & Wonderland · Station Cleaning
Checklist, effective July 27, 2026) and the "Food Safety Daily Commitment".
Seeded once on the first boot after upgrade (meta key cleaning_lists_seeded);
after that they're ordinary templates the Operator edits under Admin →
Edit checklist templates.

Each entry: (name, frequency, daypart, area, due_weekday, [(label, critical)]).
frequency is daily | weekly | monthly; daypart "allday" shows the list all
day on Today; due_weekday (0 = Monday) is the weekly "assign a day" — None
until the Operator picks one.
"""

SHEET_EFFECTIVE = "2026-07-27"

_DINING_DAILY = [
    "Clean & sanitize every table and table marker after each guest",
    "Wipe & sanitize counters, condiment stations, chair seats/backs, booster seats",
    "Clean & disinfect high-touch points (door handles, kiosks, POS screens, card readers)",
    "Wipe down the KPS monitor screen with an approved screen-safe cleaner",
    "Inspect condiments & dispensers; remove residue and damaged packages",
    "Spot-clean walls, doors, glass, windows, sills, partitions",
    "Sweep continuously as needed; vacuum carpet & mop hard floors at closing",
    "Empty trash/recycling before overflow; disinfect bin lids & touchpoints",
    "Refill hand sanitizer stations throughout the dining room",
    "Clean glass on the front double-door entrance & patio door throughout the day",
    "Clean front-counter beverage-dispenser heads/nozzles & drink-tower surfaces daily",
    "Washrooms: clean & disinfect toilets, sinks, faucets, mirrors, door handles",
    "Washrooms: refill soap, hand sanitizer, toilet paper, paper towels",
    "Washrooms: empty trash & hygiene receptacles; replace liners",
    "Washrooms: sweep & mop with washroom-designated tools only",
    "Washrooms: remove odours, standing water, or damage immediately",
    "Ensure any food photographed for social media is in a clean, tidy setting",
]

_DINING_WEEKLY = [
    "Wash interior/exterior glass doors, windows, frames, tracks, sills",
    "Detail door handles, push plates, thresholds, kick plates, baseboards, chair/table legs",
    "Dust light fixtures, vents, menu-board frames, décor, ceiling fans",
    "Shampoo/spot-extract carpet & upholstery; scrub hard-floor edges & corners",
    "Deep-clean condiment pods, beverage cabinets, tray returns, trash enclosures",
    "Deep-clean washroom partitions, walls, grout, floor edges, vents, drains",
    "Deep-clean front-counter drink-tower exteriors, ice chutes, nozzles, syrup connectors",
    "Clean the KPS monitor housing, mount, and cables; remove dust and grease buildup",
    "Wipe down dining-room walls",
]

_DINING_MONTHLY = [
    "Wash dining-room walls, baseboards, ceiling edges, décor, vents; remove cobwebs",
    "Machine-scrub & detail grout, cove base, and floor edges in dining room & washrooms",
    "Clean high-level windows, signage, light fixtures within safe-access limits",
    "Empty & sanitize front-counter beverage-dispenser ice bin",
    "Leadership cleanliness walk: dining room & washrooms — log every deficiency",
]

_DT_DAILY = [
    "Clean drive-thru windows, frames, ledges, handles, headsets",
    "Clean order points, menu-board surfaces, iPads, card readers, payment devices",
    "Wipe down the KPS monitor screen with an approved screen-safe cleaner",
    "Remove, clean & store delivery posts/signs/barriers/trays after each period",
    "Sweep drive-thru lanes, sidewalks, curbs, entrances; remove gum, spills, trip hazards",
    "Keep catering coolers, lava packs, insulated carriers clean, sanitized, dry",
    "Keep delivery vehicles clean & odour-free; complete pre-trip inspection",
    "Empty exterior bins near drive-thru before overflow; wipe lids and touchpoints",
    "Wipe payment devices, screens & touch points between uses as needed",
    "Cockpit ice machine: wipe exterior, doors, handles; verify ice is at least half full",
    "Cockpit ice machine: check bin interior for mildew/debris; store scoop/bucket off the floor",
    "Clean drive-thru Coke tower exterior, nozzles & syrup connections",
    "Clean Crathco lemonade/Sunjoy dispenser exterior; clear the filter underneath of buildup",
    "Clean the drain underneath the filter water sink in the cockpit; keep free of debris",
    "Clean & sanitize the cockpit sink(s)",
    "Clean the sugar bins (exterior and interior); keep free of spills and buildup",
    "Wipe down drive-thru walls throughout the shift",
]

_DT_WEEKLY = [
    "Wash drive-thru glass, frames, tracks, sills",
    "Remove grease, gum, weeds, and debris from lane/curb pavement",
    "Inspect drive-thru door sweeps & seals for pest-entry gaps",
    "Check exterior rodent bait stations near drive-thru door",
    "Cockpit ice machine: clean seals, gaskets, sliding windows/tracks, casters/legs",
    "Clean the KPS monitor housing, mount, and cables; remove dust and grease buildup",
    "Clean drive-thru order-taking huts (interior & exterior): sweep, wipe surfaces, check heater",
    "Wipe down walls behind the drive-thru cockpit",
]

_DT_MONTHLY = [
    "Clean high-level windows, signs, and menu boards (safe-access limits)",
    "Cockpit ice machine: clean vents/condenser coils; inspect for cracks, rust, or water pooling; log damage",
    "Clean delivery bags (interior & exterior); inspect for damage or odour",
    "Leadership cleanliness walk: drive-thru — log every deficiency",
]

# Food Safety Daily Commitment: every row has an AM and a PM box, so it's
# two daily lists. The lowboy row's four boxes are one item per lowboy.
_FS_COMMITMENT = [
    ("Lowboy clean (interior, exterior, racks & gaskets): front counter beverages lowboy", False),
    ("Lowboy clean (interior, exterior, racks & gaskets): dressings & salad lowboy", False),
    ("Lowboy clean (interior, exterior, racks & gaskets): dessert lowboy", False),
    ("Lowboy clean (interior, exterior, racks & gaskets): drive-thru beverages lowboys (2)", False),
    ("Ice machine: interior opening where ice comes out, exterior", False),
    ("Ice cream machine exterior, shake base dispenser & top of it", False),
    ("Counters and sides of steel counters", False),
    ("Drive-thru walls & sinks", False),
    ("Hand washing sink & dump sink", False),
    ("Sugar bin exterior", False),
    ("Tea brewing area and the top of it", False),
    ("All KPS screens and handles", False),
    ("Soda nozzles and whole station (weekly deep cleaning)", False),
    ("Things to notice: all cleaning supplies kept at their positions, not near food supplies", True),
    ("Things to notice: ice in the whipping cream container & cherries holder", False),
    ("Things to notice: hanging gallons have lids on", False),
    ("Things to notice: sugar cup placed upside down", False),
]


def _timed(times: list[str]) -> list[tuple[str, bool]]:
    items = []
    for t in times:
        items.append((f"{t} — sanitizer changed", True))
        items.append((f"{t} — spindles cleaned", True))
    return items


def _plain(labels: list[str]) -> list[tuple[str, bool]]:
    return [(label, False) for label in labels]


LISTS = [
    ("Dining Room & Washrooms — Daily", "daily", "allday", "Dining Room", None,
     _plain(_DINING_DAILY)),
    ("Drive Thru — Daily", "daily", "allday", "Drive Thru", None, _plain(_DT_DAILY)),
    ("Food Safety Commitment — AM", "daily", "breakfast", "Food Safety", None,
     _FS_COMMITMENT),
    ("Sanitizer & spindles — 10:30 / 12:30", "daily", "lunch", "Food Safety", None,
     _timed(["10:30", "12:30"])),
    ("Sanitizer & spindles — 2:30 / 4:30", "daily", "afternoon", "Food Safety", None,
     _timed(["2:30", "4:30"])),
    ("Food Safety Commitment — PM", "daily", "dinner", "Food Safety", None,
     _FS_COMMITMENT),
    ("Sanitizer & spindles — 6:30 / 8:30", "daily", "dinner", "Food Safety", None,
     _timed(["6:30", "8:30"])),
    ("Dining Room & Washrooms — Weekly", "weekly", "allday", "Dining Room", None,
     _plain(_DINING_WEEKLY)),
    ("Drive Thru — Weekly", "weekly", "allday", "Drive Thru", None, _plain(_DT_WEEKLY)),
    ("Dining Room & Washrooms — Monthly audit", "monthly", "allday", "Dining Room", None,
     _plain(_DINING_MONTHLY)),
    ("Drive Thru — Monthly audit", "monthly", "allday", "Drive Thru", None,
     _plain(_DT_MONTHLY)),
]

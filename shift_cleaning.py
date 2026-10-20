"""The store's posted station cleaning sheets, pinned as checklist templates.

Source: the laminated sheets on the wall (Chick-fil-A Wharncliffe &
Wonderland · Station Cleaning Checklist, effective July 27, 2026), in two
batches: front of house — "Dining Room & Washrooms", "Drive Thru" and the
"Food Safety Daily Commitment" — and back of house — "Machines", "Boards —
Primary, Secondary & Prep", "Produce Station" and "Breading & Raw". Each
batch is seeded once, on the first boot after it ships (its meta key in
BATCHES); after that they're ordinary templates the Operator edits under
Admin → Edit checklist templates.

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


# Back of house. The sheets mark nothing; "!" flags the food-safety controls
# (temperatures, sanitizer strength, raw/ready-to-eat separation, hold and
# shelf-life limits) red in the app so they're never the item skipped.
_MACHINES_DAILY = [
    "Verify fryers, grills, griddles, ovens, warmers, toasters are clean & preheated at opening",
    "Keep frying oil clean, correct colour, free of crumbs; not smoking or foaming",
    "Wipe fryer exteriors/ledges; clean baskets; empty crumb & filter pans",
    "Brush/scrape grill & griddle surfaces after use; empty grease/drip trays",
    "Empty toaster crumb trays; clean bun chutes, guards, vents",
    "Clean & sanitize Icedream machine daily; reassemble & prime before use",
    "Inspect ice-machine bin/doors/seals; remove residue, mould, or standing water",
    "Cover fryers & hot equipment at closing or during extended non-use",
    "Replace grill roller oil every four hours",
]

_MACHINES_WEEKLY = [
    "Boil out each fryer per approved procedure; discard boil-out oil (never reuse)",
    "Deep-clean grill/griddle grates, oil pans, rollers, grease troughs",
    "Deep-clean oven walls, racks, grates, vents, drip trays",
    "Clean fryer caster wheels; remove grease buildup underneath",
    "Take apart & clean the bun toaster; clear crumb trays & guards",
    "Wipe down machines-area walls",
]

_MACHINES_MONTHLY = [
    "Deep-clean Icedream machine; inspect hopper, mix pump, dispensing valve for buildup",
    "Deep-clean Grill 1 (rotate monthly)",
    "Deep-clean Grill 2 (rotate monthly)",
    "Inspect equipment cords, guards, seals, gaskets, controls; log any damage",
    "!Calibrate ovens, thermostats, timers, and temperature probes",
    "Verify hood/exhaust service interval meets fire code (NFPA 96); clean hood filters",
    "Clean condenser coils and drain pans on refrigeration tied to this line",
]

_BOARDS_DAILY = [
    "Wipe boards-area surfaces with a sanitized towel right after each transfer pan is set down",
    "Remove empty transfer pans & utensils from boards area immediately after use",
    "!Use clear gloves for all cooked/ready-to-eat handling; change when torn or soiled",
    "Wipe down low-boy reach-in fridges (lettuce, cheese, Cobb salad) inside and out; remove spills",
    "Clean the Merco holding unit(s) exterior and interior; remove crumbs & buildup",
    "!Clean the A.H.A. (Automated Holding Platform) cabinet exterior and interior; "
    "verify product held hot no longer than 20 minutes",
    "!Keep cooked-product pans off the floor and away from raw prep areas",
    "!Maintain separate sanitized cutting boards for RTE prep vs. raw prep",
    "!Wash & sanitize slicers/utensils touching room-temp food at least every 4 hours",
    "Clean & sanitize 4-cup meal-fulfillment trays at least once daily",
    "Clean handheld menus/order materials after each use or when soiled",
    "Keep condiment packages, cup rims, lids, clamshells free of residue & smudges",
    "!Label prepared food with product, date/AM-PM, initials; discard beyond shelf life",
    "Wipe spills from boards-area counters and walls immediately",
    "!Wash & sanitize hands between raw and RTE tasks",
]

_BOARDS_WEEKLY = [
    "Inspect cutting boards & utensils; remove cracked, rusted, or damaged items",
    "Deep-clean condiment pods and packaging/backsplash areas at boards stations",
    "Clean low-boy reach-in fridge gaskets/seals; check for wear or debris buildup",
    "Delime & clean the soup warmer and soup thermalizer",
    "Clean waffle fries carton dispensers",
    "Clean bun racks and stacker",
    "Clean out the fries cabinet",
    "Clean behind the secondary table",
    "Wipe down the top of the centerline",
    "Wipe down boards-area walls",
]

_BOARDS_MONTHLY = [
    "Sharpen approved knives/blades or schedule service; replace damaged boards/trays",
    "Clear debris from centerline floor drains (×4)",
    "Leadership cleanliness walk: boards & assembly stations — log every deficiency",
]

_PRODUCE_DAILY = [
    "!Verify produce is 40°F or lower; out of refrigeration no longer than 30 minutes",
    "!Test produce-wash concentration before every batch (0.75–1.0 oz/gal)",
    "!Submerge whole produce before cutting or trimming for at least 90 seconds",
    "!Wear gloves when handling washed produce; discard damaged or spoiled product",
    "Wash, rinse, sanitize & air-dry produce cutters, juicers, spinners after each use",
    "Clean the lettuce spinner thoroughly (bowl, basket, lid) — a commonly missed item",
    "!Keep the vegetable sink clean & sanitized — produce only, never dishes or raw meat",
    "Remove spills and debris from walk-in shelves, racks, walls, doors, floors",
    "Sweep & mop walk-in refrigerator/freezer and dry-storage floors",
    "Store food and paper goods 6 inches off the floor; rotate stock FIFO",
    "Wipe down the KPS monitor screen with an approved screen-safe cleaner",
]

_PRODUCE_WEEKLY = [
    "Empty one walk-in section at a time; wash/sanitize shelves, racks, walls, seals",
    "Clean dry-storage shelving, dunnage racks, carts, ingredient bins; remove clutter",
    "Deep-clean produce cutters/juicers/spinners and removable guards",
    "Wipe down the produce-station ice machine (exterior & bin interior) and surrounding equipment",
    "Clean out the blast chiller",
    "Clean out the prep table",
    "Clean cup collars",
    "Wipe clean the ORCA unit — exterior and interior",
    "Clean the KPS monitor housing, mount, and cables; remove dust and grease buildup",
    "Wipe down produce-station walls",
]

_PRODUCE_MONTHLY = [
    "Clean walk-in/reach-in drain pans and condenser filters; clean coils quarterly",
    "Deep-clean produce-station ice machine: vents/condenser coils; inspect for cracks, "
    "rust, or water pooling; log damage",
    "Leadership cleanliness walk: produce & storage areas — log every deficiency",
]

_BREADING_DAILY = [
    "!Prepare raw-designated sanitizing sink/buckets; test each fill (150–400 ppm)",
    "!Use yellow gloves and yellow-designated utensils/boards/knives/tongs only for raw chicken",
    "!Keep raw-chicken pans/containers off the floor and away from RTE prep areas",
    "!Keep the breader off fryer/grill controls; keep machines person off raw tools",
    "!Every 4 hours, clean & sanitize breading-table utensils (scoops, sifter baskets, "
    "transfer pans)",
    "!Verify refrigerated breading table 33–38°F and ice-bath display 32–37°F before loading",
    "Keep breading-table pans correctly seated with no gaps or overlap",
    "Drain ice-bath breading table at closing; sanitize the receiving floor drain after",
    "Wipe spills/moisture from breading-table rails, pans, and filters throughout the day",
    "Clean breading table exterior panels & interior compartments",
    "Wipe down filet roller exterior & interior; clear debris after each use",
    "Wipe down thaw cabinet & holding cabinet exterior and interior surfaces; remove spills",
    "!Cook chicken and egg products to an internal temperature of 165°F",
    "!Never reuse a towel that cleaned raw-chicken surfaces on any other surface",
]

_BREADING_WEEKLY = [
    "Disassemble & deep-clean breading-table rails, hinges, doors, spacers, drains",
    "Pull movable fryers near breading area; clean accessible side/floor/wall surfaces",
    "Clean caster wheels underneath filet roller, breading table, thaw & holding cabinets",
    "Wipe down breading & raw station walls",
]

_BREADING_MONTHLY = [
    "Inspect breading-table seals, gaskets & controls; log any damage for repair",
    "Leadership cleanliness walk: breading & raw station — log every deficiency",
]


def _flagged(labels: list[str]) -> list[tuple[str, bool]]:
    """Labels starting with "!" are food-safety critical (the "!" is dropped)."""
    return [(label[1:], True) if label.startswith("!") else (label, False)
            for label in labels]


FOH_LISTS = [
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

BOH_LISTS = [
    ("Machines — Daily", "daily", "allday", "Machines", None, _flagged(_MACHINES_DAILY)),
    ("Boards — Daily", "daily", "allday", "Boards", None, _flagged(_BOARDS_DAILY)),
    ("Produce Station — Daily", "daily", "allday", "Produce", None,
     _flagged(_PRODUCE_DAILY)),
    ("Breading & Raw — Daily", "daily", "allday", "Breading & Raw", None,
     _flagged(_BREADING_DAILY)),
    ("Machines — Weekly", "weekly", "allday", "Machines", None, _flagged(_MACHINES_WEEKLY)),
    ("Boards — Weekly", "weekly", "allday", "Boards", None, _flagged(_BOARDS_WEEKLY)),
    ("Produce Station — Weekly", "weekly", "allday", "Produce", None,
     _flagged(_PRODUCE_WEEKLY)),
    ("Breading & Raw — Weekly", "weekly", "allday", "Breading & Raw", None,
     _flagged(_BREADING_WEEKLY)),
    ("Machines — Monthly audit", "monthly", "allday", "Machines", None,
     _flagged(_MACHINES_MONTHLY)),
    ("Boards — Monthly audit", "monthly", "allday", "Boards", None,
     _flagged(_BOARDS_MONTHLY)),
    ("Produce Station — Monthly audit", "monthly", "allday", "Produce", None,
     _flagged(_PRODUCE_MONTHLY)),
    ("Breading & Raw — Monthly audit", "monthly", "allday", "Breading & Raw", None,
     _flagged(_BREADING_MONTHLY)),
]

# Seed batches, in order: (meta key that marks it seeded, its lists). A new
# batch gets a new key so existing databases pick it up on the next boot.
BATCHES = [
    ("cleaning_lists_seeded", FOH_LISTS),
    ("boh_cleaning_lists_seeded", BOH_LISTS),
]

LISTS = FOH_LISTS + BOH_LISTS

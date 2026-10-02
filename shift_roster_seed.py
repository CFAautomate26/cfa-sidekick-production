"""The team roster, pinned from Slack #general (C05S15A1XMF).

Seeded once into team_members on the first boot after upgrade (meta key
roster_snapshot_applied), so the 1:1 picker lists the whole team before the
Slack bot has the channels:read + users:read scopes to pull it live. The
merge rules are the live pull's (shift_db.sync_roster_from_slack): leaders
with a login are skipped, existing roster names are linked rather than
duplicated, and nobody is renamed, reactivated, or removed.

Names and Slack user ids only — nothing else from Slack. The workspace
owner (the Operator) is left out. Once the live pull works, this file is
just history: the "Pull team from Slack #general" button keeps the roster
current.
"""

SNAPSHOT_DATE = "2026-10-02"

# (slack user id, real name, display name) — names cleaned the way the live
# pull cleans them (all-lowercase words capitalized).
_MEMBERS = [
    ("U064X0QJ5FZ", "Neha Ambookkan", "NeHa"),
    ("U065957A30S", "Sarath Kanna", "Sarath"),
    ("U065NMLM7J5", "Calla Jonkman", ""),
    ("U065Q2BHRRP", "Quan Thanh Huynh", ""),
    ("U066L0PGS0N", "Erika Papastamos", ""),
    ("U06A2QWJBN3", "Chassidy Fleming", ""),
    ("U06ES9QC4KH", "Rolando Espaillat", ""),
    ("U06F0734738", "Shelly Montiyagala", ""),
    ("U06F31PUV7C", "Natausha Sims", ""),
    ("U06F8JRJLLQ", "Sammy Kennedy", ""),
    ("U06LJS5TYPP", "Tabitha Beauvais", "Tabitha Lynn"),
    ("U07ABBMJQP3", "Jubina Binu", ""),
    ("U08LDD2VB0U", "Tushar", ""),
    ("U08T2RGTTDE", "Samira Z", ""),
    ("U08TSH1URJ5", "Keira Van Dinther", "Keira"),
    ("U09ARTLCLK0", "Nicki R", ""),
    ("U09BRLFHRUK", "Pru MacLennan", ""),
    ("U09EE5MJ4R2", "Riya Ronny Thomas", "Riya"),
    ("U09MPNSUFKK", "Janeila Taylor", ""),
    ("U09N8FNAZJS", "Aron David Daniel", "Aron"),
    ("U0AA2EC3KGV", "Grace Fraser", ""),
    ("U0AA8CLLLP5", "Denay Rial", ""),
    ("U0AATMPLMLZ", "Yeguk Kim", ""),
    ("U0AB06ESM6C", "Precious Dumaguin", ""),
    ("U0AB06G99LY", "Sofia Lawrence-Page", ""),
    ("U0ACAFKPF8B", "Avery Sharpe", ""),
    ("U0ATRTWEVQW", "Jaxon", ""),
    ("U0AU6SRJNQ1", "Khesia Pious", ""),
    ("U0BB9LMU86A", "Chloe Hill", ""),
    ("U0BBT2BV4LV", "Martin Rebolledo", ""),
    ("U0BK30UL9BQ", "Ihzan", ""),
    ("U0BPP9ENZT9", "Jordyn Bischop", ""),
    ("U0BPYDL92EP", "Christina Farrer", ""),
    ("U0BTEV19BRR", "Julia", ""),
    ("U0BTH3AQ8BF", "Shenelly Montiyagala", ""),
    ("U0BTK8LU2F8", "Casee Konecny", "Casee Konecny"),
    ("U0BTK8M219U", "Mason", ""),
    ("U0C19C1NQJZ", "Shuaib", ""),
]

PEOPLE = [{"slack_id": sid, "name": name, "display": display}
          for sid, name, display in _MEMBERS]

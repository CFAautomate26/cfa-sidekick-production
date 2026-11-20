"""Leader reviews (/shift/reviews...), part of the People section.
Placeholder until the feature lands.

people.py imports this module when it is itself imported, calls
register(bp) with the People blueprint before the app registers it, and
calls nav_counts() when it builds the menu. Routes added in register(bp)
sit behind the People gate (signed-in leader, PIN re-entry), so a view only
has to check who may see its own records: people.viewer(), people.can(),
people.requires(), people.deny() and people.actor(). Name the endpoints
reviews_* (they become people.reviews_*)."""


def register(bp) -> None:
    pass


def nav_counts(kind, leader_id, name) -> dict:
    """Counts for the menu. Keys people.py reads: on_hold, past_due (the
    Operator), drafts (a reviewer), mine (any leader: what is waiting for
    them in their own review)."""
    return {}

"""Club depth: the thing a player's own minutes cannot tell you.

Minutes share says whether a player has been playing. It cannot say what
happens when the man ahead of him stops. A backup striker and a first
choice just back from injury look identical at a third of the minutes,
and when the starter is ruled out the backup's own history is exactly as
unhelpful - nothing in it has changed, but everything about his week has.

This groups every player by club and position (the API's position, which
is the one the game enforces) and hands the minutes of anyone flagged out
to the players who are fit, in proportion to what they already play. If
half a group's minutes belong to players who cannot play, whoever is left
absorbs them.

It only ever fires on a flag. With a full-strength group every multiplier
is 1.0, which is why it does not disturb the ordinary case: Wood sat
behind a fit Igor Jesus before this and still does.
"""

# how much of his usual load a player at each status can be expected to take
STATUS_WEIGHT = {"a": 1.0, "d": 0.75, "i": 0.15, "s": 0.15, "u": 0.0,
                 "n": 0.0}
MAX_BOOST = 2.0          # a backup can roughly double his minutes, not more


def status_weight(e):
    """Share of his usual minutes this player is good for."""
    st = e.get("status") or "a"
    chance = e.get("chance_of_playing_next_round")
    if chance is not None and st in ("d", "i", "s"):
        return max(0.0, min(1.0, chance / 100.0))
    return STATUS_WEIGHT.get(st, 1.0)


def multipliers(elements, shares, max_boost=MAX_BOOST):
    """{element id: scale on his minutes share}, 1.0 when nobody is out.

    shares: {element id: trailing minutes share}, None allowed for players
    with no history - they carry no weight and receive no boost.
    """
    groups = {}
    for e in elements:
        groups.setdefault((e.get("team"), e.get("element_type")), []).append(e)
    out = {}
    for members in groups.values():
        total = fit = 0.0
        for e in members:
            w = shares.get(e["id"]) or 0.0
            total += w
            fit += w * status_weight(e)
        if total <= 0 or fit <= 0:
            continue
        m = min(max_boost, total / fit)
        if m <= 1.0:
            continue
        for e in members:
            # only players who can actually play absorb the freed minutes
            if status_weight(e) >= 0.9:
                out[e["id"]] = m
    return out


def describe(elements, shares, mult, team, element_type, names=None):
    """Readable depth chart for one club and position, for diagnostics."""
    rows = [e for e in elements
            if e.get("team") == team and e.get("element_type") == element_type]
    rows.sort(key=lambda e: -(shares.get(e["id"]) or 0.0))
    return [(e.get("web_name"), shares.get(e["id"]), e.get("status"),
             mult.get(e["id"], 1.0)) for e in rows]

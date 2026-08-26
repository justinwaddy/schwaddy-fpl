"""Manual availability overrides for moves the FPL API has not posted yet.

status and news are the only live signal the model has that a player is
gone, and the API can lag a completed transfer by days. Until it catches
up, a departed player keeps his full projection and stays top of the XI -
which is worse than useless, because it is confidently wrong.

Entries here are applied to the bootstrap in memory before anything reads
it, so availability, the projections, the XI and the news feed all agree.
The on-disk copy is left as the API served it.

A stale override silently outranks real data, so refresh prints every one
it applies and each entry carries the date it was added. Delete an entry
once the API carries the news itself.
"""

# player code -> (status, news). Status codes match the API's own:
#   "u" unavailable, "i" injured, "s" suspended, "d" doubtful, "a" fit.
OVERRIDES = {
    # Ollie Watkins - moved to the Saudi Pro League, added 2026-08-26.
    # The API still had him at status "a" with no news the morning after.
    "178301": ("u", "Has left the Premier League"),
}


def apply(bootstrap):
    """Force status and news on overridden players. Returns what changed."""
    hit = []
    for e in bootstrap.get("elements", []):
        ov = OVERRIDES.get(str(e.get("code")))
        if ov is None:
            continue
        e["status"], e["news"] = ov
        e["chance_of_playing_next_round"] = None
        e["chance_of_playing_this_round"] = None
        hit.append(e.get("web_name") or e.get("code"))
    return hit

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
once the API carries the news itself - and because "delete it once the
API catches up" is a rule nobody remembers to follow, apply() checks for
that itself and says so in the line it prints. Two ways an entry stops
being needed:

- the API's own status now matches the override, so forcing it is a no-op
- the player is gone from the bootstrap entirely, which is what usually
  happens to someone who leaves the league, and which means the entry is
  now doing nothing at all

Either way the printed line asks for the entry to be deleted, and says
how long it has been carried.
"""
from datetime import date

# player code -> (status, news, date added). Status codes match the API's
# own: "u" unavailable, "i" injured, "s" suspended, "d" doubtful, "a" fit.
OVERRIDES = {
    # Ollie Watkins - moved to the Saudi Pro League.
    # The API still had him at status "a" with no news the morning after.
    "178301": ("u", "Has left the Premier League", "2026-08-26"),
}


def _age(added):
    try:
        d0 = date.fromisoformat(added)
    except (TypeError, ValueError):
        return ""
    n = (date.today() - d0).days
    return f", carried {n} day{'' if n == 1 else 's'}"


def apply(bootstrap):
    """Force status and news on overridden players.

    Returns one line per entry, ready to print: what it did, or that the
    entry can now be deleted.
    """
    lines, seen = [], set()
    for e in bootstrap.get("elements", []):
        code = str(e.get("code"))
        ov = OVERRIDES.get(code)
        if ov is None:
            continue
        seen.add(code)
        status, news, added = ov
        name = e.get("web_name") or code
        was = e.get("status")
        e["status"], e["news"] = status, news
        e["chance_of_playing_next_round"] = None
        e["chance_of_playing_this_round"] = None
        if was == status:
            lines.append(f"{name} -> {status}: the API now says this itself"
                         f"{_age(added)}. DELETE this entry.")
        else:
            lines.append(f"{name} -> {status} ({news}), was {was}{_age(added)}")
    for code, ov in OVERRIDES.items():
        if code not in seen:
            lines.append(f"code {code} is no longer in the bootstrap"
                         f"{_age(ov[2])}, so this entry is doing nothing. "
                         f"DELETE it.")
    return lines

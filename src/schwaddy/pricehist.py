"""A rolling record of classic-game prices: data/price_history.json.

data/prices.json is rebuilt from the API on every run and so knows only
two things about a price: what it is now, and how far it has moved since
the season started. Neither answers the question a manager actually asks
on a Thursday morning - who moved last night, and who has been drifting
all week. That needs yesterday's price, and last week's, and the API does
not serve either. So we keep them.

FPL reprices once a day, at about 01:30 UK, and every refresh run falls
after it, so one snapshot per calendar day (UTC) is the whole story: two
runs on the same day see the same price. The file holds the last KEEP
days as columns and every player as a row of prices aligned to them,
which keeps it under a hundred kilobytes for the whole game.

    {"generated", "days": ["YYYY-MM-DD", ...],
     "players": {player code: [price on each day, null before he existed]}}

Unlike every other file under data/ this one is cumulative: it cannot be
regenerated from the API, only extended. If it is ever lost the columns
that depend on it go blank and refill over the following week - or run
`python -m schwaddy.pricehist --seed` to rebuild it from the committed
history of data/prices.json, which is the same snapshots by another name.
"""
import json
import os
import subprocess
from datetime import date, datetime, timedelta, timezone

# Days of snapshots kept. Seven for the week column, plus slack so a run
# that misses a day still has something to reach back to.
KEEP = 15
# How far either side of seven days we will still call a week, and the
# shortest span we will report as one. Below two days the week column
# would only be repeating the 24-hour column, so it stays blank instead.
WEEK, WEEK_SLACK, WEEK_MIN = 7, 2, 2
# FPL reprices at 01:30 UK, which is 00:30 UTC in summer and 01:30 in
# winter, so a UTC date does not become settled until the small hours. The
# scheduled runs are all well clear of that, but the GitHub backstop has
# come in hours late before, and a run at 00:20 would file yesterday's
# prices under today's date and swallow a day of movement. Runs before
# this hour read the history but do not add to it.
SETTLED_UTC_HOUR = 3


def _empty():
    return {"days": [], "players": {}}


def load(data_dir):
    try:
        h = json.load(open(f"{data_dir}/price_history.json", encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(h.get("days"), list) or not isinstance(h.get("players"), dict):
        return _empty()
    return h


def _unpack(h):
    """History as one {code: price} map per day, easier to edit than rows."""
    days = list(h["days"])
    maps = [{} for _ in days]
    for code, arr in h["players"].items():
        for i, v in enumerate(arr or []):
            if i < len(days) and v is not None:
                maps[i][str(code)] = v
    return days, maps


def _pack(days, maps):
    codes = sorted({c for m in maps for c in m})
    return dict(generated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                days=days, players={c: [m.get(c) for m in maps] for c in codes})


def _day(s):
    return date.fromisoformat(s)


def _deltas(days, maps, now, today):
    """(per-code [24h, week] moves, the spans in days those cover).

    The day each column reaches back to is chosen once for everybody, so
    the spans describe the whole table and the site can say plainly what
    the week column is really covering while the file is still filling up.
    """
    past = [(i, _day(d)) for i, d in enumerate(days) if _day(d) < today]
    yday = next((i for i, d in past if (today - d).days == 1), None)
    # The nearest thing to a week ago that is neither yesterday's price
    # under another name nor a fortnight of drift dressed up as a week.
    week = min((p for p in past if WEEK_MIN <= (today - p[1]).days <= WEEK + WEEK_SLACK),
               key=lambda p: abs((today - p[1]).days - WEEK), default=None)
    spans = {"d1": 1 if yday is not None else None,
             "d7": (today - week[1]).days if week else None}
    out = {}
    for code, p in now.items():
        was1 = maps[yday].get(code) if yday is not None else None
        was7 = maps[week[0]].get(code) if week else None
        out[code] = [None if was1 is None else round(p - was1, 1),
                     None if was7 is None else round(p - was7, 1)]
    return out, spans


def update(data_dir, now, today=None):
    """Fold today's prices into the history and read the moves back out.

    `now` is {player code: price}. Returns the same (deltas, spans) as
    _deltas. Today's own column is rewritten on every run of the day,
    which costs nothing: the price cannot have moved since the last one.
    """
    stamp = datetime.now(timezone.utc)
    today = today or stamp.date()
    days, maps = _unpack(load(data_dir))
    if stamp.hour >= SETTLED_UTC_HOUR or today != stamp.date():
        key = today.isoformat()
        if key in days:
            maps[days.index(key)] = dict(now)
        else:
            days.append(key)
            maps.append(dict(now))
            order = sorted(range(len(days)), key=lambda i: days[i])
            days, maps = [days[i] for i in order], [maps[i] for i in order]
        days, maps = days[-KEEP:], maps[-KEEP:]
        json.dump(_pack(days, maps), open(f"{data_dir}/price_history.json", "w"),
                  separators=(",", ":"))
    # _deltas keeps only the days before today, so this reads the same
    # whether or not today's own column has been filed yet.
    return _deltas(days, maps, now, today)


# ---- rebuilding from git -------------------------------------------------
# The refresh run commits data/prices.json after every pass, so the repo
# already holds every snapshot this file wants; it just holds them one
# commit at a time. Walking that log back is how the history was seeded in
# the first place, and how it would be recovered if the file were lost.

def _git(*args):
    return subprocess.run(["git"] + list(args), capture_output=True, text=True,
                          check=True).stdout


def seed(data_dir, keep=KEEP):
    """Rebuild data/price_history.json from the git log of prices.json."""
    log = _git("log", "--format=%H %ad", "--date=format-local:%Y-%m-%d",
               "--", f"{data_dir}/prices.json").split("\n")
    # The log runs newest first, so the first commit seen for a date is
    # that day's last run - the one whose prices stood at day's end.
    pick = {}
    for line in log:
        if not line.strip():
            continue
        sha, day = line.split()
        pick.setdefault(day, sha)
    days = sorted(pick)[-keep:]
    maps = []
    for day in days:
        blob = json.loads(_git("show", f"{pick[day]}:{data_dir}/prices.json"))
        maps.append({str(c): r[0] for c, r in (blob.get("players") or {}).items()})
    json.dump(_pack(days, maps), open(f"{data_dir}/price_history.json", "w"),
              separators=(",", ":"))
    return days


if __name__ == "__main__":
    import sys
    d = os.environ.get("DATA_DIR", "../data")
    if "--seed" in sys.argv:
        print("seeded", ", ".join(seed(d)))
    else:
        print(json.dumps(load(d).get("days", []), indent=2))

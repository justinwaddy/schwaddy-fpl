"""Current-season minutes, read from the draft API instead of the archive.

The public gameweek archive (vaastav) does not publish the live season
until weeks into it, so panel.build() runs with no live-season rows at all
and availability falls back on last season's trailing matches. That is
badly wrong for anyone whose role changed over the summer: a player who
has been an unused sub all season still scores as a nailed starter.

The draft API carries per-gameweek minutes in its live endpoints from
match one, so this module reads them directly and splices them onto the
archive. Only availability uses them - the panel's Y and D, and so the
fitted projection, are left alone.

Minutes also fix a second failure: the archive's D is a bare appearance
indicator, so a 15-minute cameo counted the same as a full start. The
share below is minutes played over minutes available, which does not.
"""
from . import api

WINDOW = 8               # trailing matches behind the availability estimate
FULL = 90.0              # minutes in a match

# Weight on a previous-season match inside the window. The window used to
# treat last May as evidence equal to last Saturday, which drowns the
# opening weeks of a season in a squad that no longer exists. Measured on
# the opening 8 gameweeks across four season transitions, dropping it to
# 0.1 cuts the Brier score by 14% overall and 31% at gameweek 2, improving
# all four transitions at every weight tried. The curve is flat from 0.15
# down to 0, so this sits at an interior value rather than the raw optimum
# of 0.05. From gameweek 9 the window is all current-season and this has
# no effect at all; with no current-season data it cancels, since it
# scales the numerator and denominator alike.
PREV_SEASON_WEIGHT = 0.10


def settled(f):
    """Is this fixture over? finished_provisional flips when the whistle
    goes; finished waits on bonus points. For anything asking whether a
    player can still add minutes or appear again, the whistle is the
    question - a squad reading "11 still to play" an hour after kick-off
    because bonus had not landed was the bug this exists to prevent.
    """
    return bool(f.get("finished") or f.get("finished_provisional"))


def played_gws(fixtures, window=WINDOW):
    """0-based gameweek indices with a played fixture, most recent last."""
    gws = sorted({f["event"] - 1 for f in fixtures
                  if f.get("event") and settled(f)})
    return gws[-window:]


def team_matches(fixtures):
    """{(team id, 0-based gw): finished matches}, so blanks and doubles
    change the minutes a player could have played."""
    out = {}
    for f in fixtures:
        if not f.get("event") or not settled(f):
            continue
        gw = f["event"] - 1
        for t in (f["team_h"], f["team_a"]):
            out[(t, gw)] = out.get((t, gw), 0) + 1
    return out


def fetch_minutes(gws):
    """(gameweeks actually loaded, {element id: {gw: minutes}}).

    One request per gameweek, capped at WINDOW by played_gws. A gameweek
    that fails to load is dropped from the returned list rather than left
    in it: a missing gameweek would otherwise read as nobody having played
    that week, and mark the entire league as dropped on one bad request.
    """
    out, loaded = {}, []
    for gw in gws:
        try:
            els = api.get(f"{api.DRAFT}/event/{gw + 1}/live")["elements"]
        except Exception:
            continue
        if not els:
            continue
        loaded.append(gw)
        for k, v in els.items():
            mins = (v.get("stats") or {}).get("minutes", 0)
            out.setdefault(int(k), {})[gw] = mins
    return loaded, out


def load(data_dir):
    """(played gameweeks, minutes by element, matches by team-gameweek).

    Returns empty structures when the live data cannot be read, which puts
    availability back on the archive-only path.
    """
    import json
    try:
        fx = json.load(open(f"{data_dir}/fixtures_2627.json"))
    except Exception:
        return [], {}, {}
    gws = played_gws(fx)
    if not gws:
        return [], {}, {}
    loaded, mins = fetch_minutes(gws)
    if not loaded or not mins:      # live lookup down: stay on the archive
        return [], {}, {}
    return loaded, mins, team_matches(fx)


def trailing_share(mins_row, m_row, d_row, n_hist, team, played, live_mins,
                   live_matches, window=WINDOW):
    """Minutes played over minutes available across the trailing window.

    Live gameweeks fill the window first, most recent first; whatever is
    left is filled backwards through last season at PREV_SEASON_WEIGHT.
    Once the season is `window` matches old the archive drops out
    entirely.

    None only when the window carries no evidence either way - no archive
    minutes and no live gameweek - where p_plays falls back to its own
    debutant prior. A player the live data shows has not featured scores
    0.0 rather than None: that is evidence of being dropped, and must not
    round up to the prior for someone never seen at all.
    """
    cells = []                                  # (minutes, minutes available)
    live_seen = 0
    for gw in reversed(played[-window:]):
        nm = live_matches.get((team, gw), 0)
        if nm:                                  # blank gameweek: nothing owed
            cells.append((live_mins.get(gw, 0), FULL * nm))
            live_seen += 1
    # Fill what is left from last season - but only for players who were in
    # the league to play it. Five clubs come up each summer; charging their
    # squads a full match of missed minutes for every archive column reads
    # absence of evidence as evidence of being dropped, and buries nailed
    # starters at promoted clubs. Anyone else falls back on live data alone.
    base = n_hist * 38
    prev = base - 38
    if d_row[prev:base].sum() > 0:
        need = window - len(cells)
        w = PREV_SEASON_WEIGHT
        for col in range(base - 1, max(prev - 1, base - 1 - need), -1):
            cells.append((w * mins_row[col], w * FULL * max(1.0, m_row[col])))
    avail = sum(a for _, a in cells)
    got = sum(m for m, _ in cells)
    if avail <= 0 or (got <= 0 and not live_seen):
        return None
    return min(1.0, got / avail)

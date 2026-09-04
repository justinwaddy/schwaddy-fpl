"""Per-player card data for the dashboard: season stats and a match log.

predictions.json carries what the model thinks a player will do. Nothing
carried what he has actually done, so clicking a name on the site had
nothing to open. This writes data/player_stats.json: season totals and
set-piece duties straight off the draft bootstrap, plus this season's
match-by-match rows read out of the gameweek file.

The site fetches this on load, so it is kept deliberately small. The log
holds appearances only - the gameweek file carries a row per player per
match whether he played or not, and fifteen rows of zeros is not a match
log - capped at the last LOG_MATCHES of them, and stored as bare arrays
with a `log_cols` header rather than a dict per row, which is about a
third of the bytes for the same numbers.

data/player_stats.json layout:
    {"generated", "season", "log_cols": [...],
     "teams":   {team id: [short name, club name]},
     "players": {player code: {id, name, full, team, pos, status, news,
                               news_added, s: {season totals}, sp: {set
                               pieces}, log: [[...], ...]}}}
Keyed by code, matching predictions.json; `id` is the element id, which is
what league.json's squads carry, so the site can resolve either.
"""
import csv
import json
import os
from datetime import datetime, timezone

from .panel import LIVE

# match log depth. A card wants recent form, not an archive, and the file
# is fetched on every page load.
LOG_MATCHES = 15

ETYPE = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

# season totals lifted from the bootstrap element. Counting stats stay
# ints; the API hands the expected-goal family and the ICT family back as
# strings, so they are floated on the way out.
INT_STATS = ["minutes", "starts", "total_points", "goals_scored", "assists",
             "clean_sheets", "goals_conceded", "own_goals", "penalties_saved",
             "penalties_missed", "yellow_cards", "red_cards", "saves",
             "bonus", "bps", "defensive_contribution", "recoveries",
             "tackles", "clearances_blocks_interceptions", "dreamteam_count"]
FLOAT_STATS = ["expected_goals", "expected_assists",
               "expected_goal_involvements", "expected_goals_conceded",
               "influence", "creativity", "threat", "ict_index",
               "form", "points_per_game"]

# match log columns, in array order
LOG_COLS = ["gw", "opp", "home", "min", "pts", "g", "a", "cs", "gc", "sv",
            "b", "bps", "xg", "xa"]
# gameweek-file column each one reads, where the names differ
LOG_SRC = {"min": "minutes", "pts": "total_points", "g": "goals_scored",
           "a": "assists", "cs": "clean_sheets", "gc": "goals_conceded",
           "sv": "saves", "b": "bonus", "bps": "bps",
           "xg": "expected_goals", "xa": "expected_assists"}
LOG_FLOAT = {"xg", "xa"}


def _num(v, cast, default=0):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return default


def _f2(v):
    """Two decimal places, or 0.0 - the card never shows more than that."""
    return round(_num(v, float, 0.0), 2)


def _log_rows(data_dir, season=LIVE, id_of_name=None):
    """{element id: [row, ...]} for the current season, newest gameweek last.

    Missing file, or a reconstruction that predates a column, is not fatal:
    a player with no rows simply gets no match log on his card.

    The `element` column is the classic game's id. It agrees with the draft
    id for everyone registered before the season - the two games number
    that intake identically - and diverges for players added later, 52 of
    them this season. So the row is keyed by the draft id its full name
    resolves to, and only falls back to the raw column when the name is
    unknown. Keying on the column alone silently filed Matt Targett's
    matches under Mamadou Sangaré.
    """
    path = f"{data_dir}/gws_{season}.csv"
    if not os.path.exists(path):
        return {}
    names = id_of_name or {}
    by_el = {}
    with open(path) as fh:
        for r in csv.DictReader(fh):
            eid = names.get((r.get("name") or "").strip().lower()) \
                or _num(r.get("element"), int, None)
            gw = _num(r.get("GW") or r.get("round"), int, None)
            if eid is None or gw is None:
                continue
            if not _num(r.get("minutes"), int, 0):
                continue               # squad rows for a match he sat out
            row = []
            for c in LOG_COLS:
                if c == "gw":
                    row.append(gw)
                elif c == "opp":
                    row.append(_num(r.get("opponent_team"), int, 0))
                elif c == "home":
                    row.append(1 if str(r.get("was_home")).lower()
                               in ("true", "1") else 0)
                elif c in LOG_FLOAT:
                    row.append(_f2(r.get(LOG_SRC[c])))
                else:
                    row.append(_num(r.get(LOG_SRC[c]), int, 0))
            by_el.setdefault(eid, []).append(row)
    gwi, mini = LOG_COLS.index("gw"), LOG_COLS.index("min")
    for eid, rows in list(by_el.items()):
        played = sorted((x for x in rows if x[mini] > 0), key=lambda x: x[gwi])
        if played:
            by_el[eid] = played[-LOG_MATCHES:]
        else:
            del by_el[eid]
    return by_el


def _set_pieces(e):
    """Penalty, corner and free-kick duties, only where the API states one."""
    sp = {}
    if e.get("penalties_order") is not None:
        sp["pen"] = e["penalties_order"]
    for key, src in (("pen_text", "penalties_text"),
                     ("ck_text", "corners_and_indirect_freekicks_text"),
                     ("fk_text", "direct_freekicks_text")):
        if e.get(src):
            sp[key] = e[src][:80]
    if e.get("corners_and_indirect_freekicks_order") is not None:
        sp["ck"] = e["corners_and_indirect_freekicks_order"]
    if e.get("direct_freekicks_order") is not None:
        sp["fk"] = e["direct_freekicks_order"]
    return sp


def build(data_dir, bootstrap=None):
    if bootstrap is None:
        bootstrap = json.load(open(f"{data_dir}/draft_bootstrap.json"))
    id_of_name = {}
    for e in bootstrap["elements"]:
        full = " ".join(x for x in (e.get("first_name"),
                                    e.get("second_name")) if x).strip().lower()
        if full:
            id_of_name[full] = e["id"]
    logs = _log_rows(data_dir, id_of_name=id_of_name)
    players = {}
    for e in bootstrap["elements"]:
        # zeros are dropped: two thirds of the grid is a zero in August
        # and the card reads a missing key as one anyway
        s = {k: _num(e.get(k), int, 0) for k in INT_STATS
             if _num(e.get(k), int, 0)}
        s.update({k: _f2(e.get(k)) for k in FLOAT_STATS if _f2(e.get(k))})
        full = " ".join(x for x in (e.get("first_name"),
                                    e.get("second_name")) if x).strip()
        p = dict(id=e["id"], name=e["web_name"], team=e["team"],
                 pos=ETYPE.get(e["element_type"], "MID"),
                 status=e.get("status", "a"), news=(e.get("news") or "")[:200],
                 s=s, log=logs.get(e["id"], []))
        if full and full != e["web_name"]:
            p["full"] = full
        if e.get("news_added"):
            p["news_added"] = e["news_added"][:16]
        if e.get("chance_of_playing_next_round") is not None:
            p["chance"] = e["chance_of_playing_next_round"]
        if e.get("draft_rank") is not None:
            p["draft_rank"] = e["draft_rank"]
        sp = _set_pieces(e)
        if sp:
            p["sp"] = sp
        players[str(e["code"])] = p
    return dict(generated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                season=LIVE, log_cols=LOG_COLS,
                teams={str(t["id"]): [t["short_name"], t["name"]]
                       for t in bootstrap["teams"]},
                players=players)


def write(data_dir, bootstrap=None):
    out = build(data_dir, bootstrap)
    json.dump(out, open(f"{data_dir}/player_stats.json", "w"))
    return out

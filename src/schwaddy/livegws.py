"""Reconstruct the current season's gameweek file from the FPL API.

The public archive (vaastav) does not publish a season's per-gameweek rows
until well into it, so panel.build() spends the opening weeks fitting on
last season and earlier. Measured rolling-origin over 25/26, that costs
2.95 realized XI points a gameweek across the season and 6.40 over its
first ten - the model is blind exactly when squads have just changed.

Everything the archive file holds is already served live by the API, so
this writes the same file from it. Nothing downstream changes: build()
reads gws_<season>.csv as it always has, and once the real archive appears
it takes over untouched.

Scoring uses the aggregate per-gameweek stats. Where a club plays twice in
a gameweek the API's own per-fixture breakdown splits them, matching the
archive's one-row-per-fixture shape; without it the gameweek collapses to
a single row, which understates the match count but never the points.
"""
import csv
import json
import os

from . import api
from .panel import LIVE

# archive column order, as build() and _team_conceded expect to find them
COLUMNS = ["name", "position", "team", "element", "GW", "fixture", "round",
           "minutes", "starts", "total_points", "goals_scored", "assists",
           "clean_sheets", "goals_conceded", "own_goals", "penalties_saved",
           "penalties_missed", "yellow_cards", "red_cards", "saves", "bonus",
           "bps", "influence", "creativity", "threat", "ict_index",
           "expected_goals", "expected_assists", "expected_goal_involvements",
           "expected_goals_conceded", "defensive_contribution",
           "was_home", "opponent_team", "team_h_score", "team_a_score",
           "kickoff_time", "value", "transfers_balance", "transfers_in",
           "transfers_out", "selected"]
STATS = [c for c in COLUMNS if c not in
         ("name", "position", "team", "element", "GW", "fixture", "round",
          "was_home", "opponent_team", "team_h_score", "team_a_score",
          "kickoff_time", "value", "transfers_balance", "transfers_in",
          "transfers_out", "selected")]
ETYPE = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def _explain_values(entry):
    """{fixture id: {stat: value}} from the API's per-fixture breakdown."""
    out = {}
    for block in entry.get("explain") or []:
        fid = block.get("fixture")
        if fid is None:
            continue
        vals = {}
        for st in block.get("stats") or []:
            ident = st.get("identifier")
            if ident is not None:
                vals[ident] = st.get("value", 0)
        out[fid] = vals
    return out


def gw_rows(gw, elements, fixtures, players, team_name):
    """Archive-shaped rows for one gameweek.

    gw is 1-based. elements is the live payload's element map. players maps
    element id -> (name, element_type, team id).
    """
    by_team = {}
    for f in fixtures:
        if f.get("event") != gw or not f.get("finished"):
            continue
        for t in (f["team_h"], f["team_a"]):
            by_team.setdefault(t, []).append(f)
    rows = []
    for eid, entry in elements.items():
        eid = int(eid)
        who = players.get(eid)
        if who is None:
            continue
        name, etype, team = who
        played = by_team.get(team) or []
        if not played:
            continue
        stats = entry.get("stats") or {}
        if int(stats.get("minutes") or 0) == 0:
            continue                      # the archive omits non-appearances
        per_fixture = _explain_values(entry) if len(played) > 1 else {}
        for f in played:
            vals = per_fixture.get(f["id"])
            if len(played) > 1 and vals is None:
                continue                  # breakdown missing: fold into one row
            src = vals if vals is not None else stats
            if int(src.get("minutes") or 0) == 0:
                continue
            home = f["team_h"] == team
            row = {c: "" for c in COLUMNS}
            row.update(name=name, position=ETYPE.get(etype, "MID"),
                       team=team_name.get(team, ""), element=eid,
                       GW=gw, round=gw, fixture=f["id"],
                       was_home=str(home),
                       opponent_team=(f["team_a"] if home else f["team_h"]),
                       team_h_score=f.get("team_h_score", ""),
                       team_a_score=f.get("team_a_score", ""),
                       kickoff_time=f.get("kickoff_time", ""))
            for k in STATS:
                row[k] = src.get(k, 0)
            rows.append(row)
        if len(played) > 1 and not per_fixture:
            f = played[0]                 # no breakdown: one row, aggregate
            home = f["team_h"] == team
            row = {c: "" for c in COLUMNS}
            row.update(name=name, position=ETYPE.get(etype, "MID"),
                       team=team_name.get(team, ""), element=eid,
                       GW=gw, round=gw, fixture=f["id"],
                       was_home=str(home),
                       opponent_team=(f["team_a"] if home else f["team_h"]),
                       team_h_score=f.get("team_h_score", ""),
                       team_a_score=f.get("team_a_score", ""),
                       kickoff_time=f.get("kickoff_time", ""))
            for k in STATS:
                row[k] = stats.get(k, 0)
            rows.append(row)
    return rows


def load_players(data_dir, season=LIVE):
    """element id -> (name, element_type, team id) from the season's raw file."""
    path = f"{data_dir}/players_raw_{season}.csv"
    if not os.path.exists(path):
        return {}
    out = {}
    for r in csv.DictReader(open(path)):
        try:
            out[int(r["id"])] = (
                f"{r.get('first_name','')} {r.get('second_name','')}".strip(),
                int(r["element_type"]), int(r["team"]))
        except (KeyError, ValueError):
            continue
    return out


def load_team_names(data_dir, season=LIVE):
    path = f"{data_dir}/teams_{season}.csv"
    if not os.path.exists(path):
        return {}
    return {int(r["id"]): r["name"] for r in csv.DictReader(open(path))}


def build_rows(data_dir, fixtures, season=LIVE, fetch=None, want=None):
    """Archive-shaped rows for the finished gameweeks in `want`."""
    players = load_players(data_dir, season)
    names = load_team_names(data_dir, season)
    if not players or not names:
        return []
    fetch = fetch or api.classic_live
    gws = sorted({f["event"] for f in fixtures
                  if f.get("event") and f.get("finished")})
    if want is not None:
        gws = [g for g in gws if g in want]
    rows = []
    for gw in gws:
        try:
            payload = fetch(gw)
        except Exception:
            continue                      # skip the gameweek, keep the rest
        elements = payload.get("elements")
        if isinstance(elements, list):    # some payloads use a list
            elements = {e.get("id"): e for e in elements if e.get("id")}
        if not elements:
            continue
        rows.extend(gw_rows(gw, elements, fixtures, players, names))
    return rows


def write(data_dir, fixtures, season=LIVE, fetch=None):
    """Write gws_<season>.csv from the API. Returns rows written.

    Settled gameweeks are kept as first written rather than rebuilt. The
    club a player belongs to comes from the season's raw file, which holds
    only his current one, so rewriting an old gameweek after he moves in
    January would file those matches under the wrong club and hand them
    the wrong opponent. The newest finished gameweek is always refetched,
    since bonus and stat corrections land late.

    Writes nothing when the reconstruction comes back empty, so a failed
    lookup leaves build() on the archive-only path it used before.
    """
    path = f"{data_dir}/gws_{season}.csv"
    kept, have = [], set()
    finished = sorted({f["event"] for f in fixtures
                       if f.get("event") and f.get("finished")})
    newest = finished[-1] if finished else None
    if os.path.exists(path):
        try:
            for r in csv.DictReader(open(path)):
                gw = int(float(r["GW"]))
                if gw == newest:          # refetch: late corrections land here
                    continue
                kept.append(r)
                have.add(gw)
        except Exception:
            kept, have = [], set()        # unreadable: rebuild from scratch
    want = [g for g in finished if g not in have]
    fresh = build_rows(data_dir, fixtures, season, fetch, want=want)
    if not fresh and not kept:
        return 0
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(kept)
        w.writerows(fresh)
    os.replace(tmp, path)
    return len(kept) + len(fresh)

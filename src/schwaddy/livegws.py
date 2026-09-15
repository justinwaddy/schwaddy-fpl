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


MARKET = ("value", "selected", "transfers_balance", "transfers_in",
          "transfers_out")


def market_from_bootstrap(boot):
    """element id -> the archive's five market columns, from the classic
    game's bootstrap. The live per-gameweek endpoint carries none of them,
    which is why every row after gameweek 1 used to leave them blank.
    selected is a count in the archive and a percentage in the bootstrap;
    total_players turns one into the other."""
    total = float(boot.get("total_players") or 0)
    out = {}
    for e in boot.get("elements", []):
        try:
            pct = float(e.get("selected_by_percent") or 0)
            ti = int(e.get("transfers_in_event") or 0)
            to = int(e.get("transfers_out_event") or 0)
            out[int(e["id"])] = dict(
                value=int(e.get("now_cost") or 0),
                selected=int(round(pct / 100.0 * total)) if total else "",
                transfers_balance=ti - to, transfers_in=ti, transfers_out=to)
        except (TypeError, ValueError, KeyError):
            continue
    return out


def gw_rows(gw, elements, fixtures, players, team_name, market=None):
    """Archive-shaped rows for one gameweek.

    gw is 1-based. elements is the live payload's element map. players maps
    element id -> (name, element_type, team id). market, if given, maps
    element id -> the five market columns as of now; a row written without
    it leaves them blank, which downstream reads as "no change".
    """
    market = market or {}
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
            # only appearances are written. The archive itself carries a
            # row for every registered player in every gameweek (about
            # three fifths of its rows are zero-minute ones), and takes
            # these gameweeks over once it publishes them; until then a
            # non-appearance simply has no row here, which every reader
            # in src/ and evo/ treats the same as a zero-minute row
            continue
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
            row.update(market.get(eid, {}))
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
            row.update(market.get(eid, {}))
            rows.append(row)
    return rows


def complete_players(data_dir, boot, season=LIVE):
    """Add to players_raw_<season>.csv every element of the classic
    bootstrap the file lacks. Returns how many were added.

    The file is the archive's copy of that same bootstrap, taken when
    the archive last looked, and the archive looks rarely: on 15
    September 2026 it was 616 players against the game's 659, the
    forty-three being every summer signing registered after its
    snapshot. Everything here keys the reconstruction on that file, so a
    late signing had no gameweek rows at all - Barcola started and
    played 71 minutes in GW4 and the season file said he had never
    played, the history file auto-subbed him out of a squad he had
    scored in, and the models read him as a man with no minutes. The
    columns are the bootstrap's own fields, so a missing player is
    written from the element as it stands; the archive's rows are kept
    as they are.
    """
    path = f"{data_dir}/players_raw_{season}.csv"
    if not os.path.exists(path):
        return 0
    with open(path, newline="") as fh:
        rd = csv.reader(fh)
        header = next(rd)
        have = {row[header.index("code")] for row in rd if row}
    add = [e for e in boot.get("elements") or []
           if str(e.get("code")) not in have]
    if not add:
        return 0
    with open(path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        for e in add:
            w.writerow({c: ("" if e.get(c) is None else e.get(c))
                        for c in header})
    return len(add)


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


def build_rows(data_dir, fixtures, season=LIVE, fetch=None, want=None,
               market=None):
    """Archive-shaped rows for the finished gameweeks in `want`."""
    players = load_players(data_dir, season)
    names = load_team_names(data_dir, season)
    if not players or not names:
        return []
    fetch = fetch or api.classic_live
    if market is None:
        # the market as of now, for the rows written now. A failure here
        # must never cost the rows themselves.
        try:
            market = market_from_bootstrap(api.classic_bootstrap())
        except Exception:
            market = {}
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
        rows.extend(gw_rows(gw, elements, fixtures, players, names, market))
    return rows


def write(data_dir, fixtures, season=LIVE, fetch=None):
    """Write gws_<season>.csv from the API. Returns rows written.

    Settled gameweeks keep every row as first written rather than being
    rebuilt. The club a player belongs to comes from the season's raw
    file, which holds only his current one, so rewriting an old gameweek
    after he moves in January would file those matches under the wrong
    club and hand them the wrong opponent. A settled gameweek does gain
    rows for a player who had none, which is what a late registration
    looks like once the raw file carries him. The newest finished
    gameweek is always refetched, since bonus and stat corrections land
    late.

    Writes nothing when the reconstruction comes back empty, so a failed
    lookup leaves build() on the archive-only path it used before.
    """
    path = f"{data_dir}/gws_{season}.csv"
    kept, seen = [], set()
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
                seen.add((gw, str(r.get("element")), str(r.get("fixture"))))
        except Exception:
            kept, seen = [], set()        # unreadable: rebuild from scratch
    # every finished gameweek is fetched, but a settled one only ever
    # GAINS rows: a player who had none when the week was written - a
    # signing the players file did not carry yet (see complete_players)
    # - gets his, and every row already there stays as it was written
    fresh = [r for r in build_rows(data_dir, fixtures, season, fetch,
                                   want=finished)
             if int(r["GW"]) == newest
             or (int(r["GW"]), str(r["element"]), str(r["fixture"]))
             not in seen]
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

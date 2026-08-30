"""Live weekly league state, written to data/league.json.

The dashboard is a static page reading committed JSON, so everything it
needs about a gameweek in progress has to be computed here: each
manager's live score with provisional substitutions applied, how many of
his starters are still to come, what the week projects to once they play,
and his full squad with per-player detail for the click-through.

Substitutions are provisional. The game only applies them when the
gameweek ends, but a starter whose match has finished with no minutes is
already lost, so his replacement is worked out here under the same
formation rules the game enforces. That makes the live table read like
the final one rather than punishing a manager for a blank he has already
covered on the bench.
"""
import json
import os
from datetime import datetime, timezone

from . import api
from .league import MY_ENTRY

# draft squad rules; overridden from the bootstrap's own settings when present
RULES = dict(play=11, min_GKP=1, max_GKP=1, min_DEF=3, max_DEF=5,
             min_MID=2, max_MID=5, min_FWD=1, max_FWD=3)
ETYPE = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def _rules(bootstrap):
    s = (bootstrap.get("settings") or {}).get("squad") or {}
    r = dict(RULES)
    for k in ("GKP", "DEF", "MID", "FWD"):
        for b in ("min", "max"):
            v = s.get(f"{b}_play_{k}")
            if isinstance(v, int):
                r[f"{b}_{k}"] = v
    if isinstance(s.get("play"), int):
        r["play"] = s["play"]
    return r


def _legal(counts, rules):
    return all(rules[f"min_{k}"] <= counts.get(k, 0) <= rules[f"max_{k}"]
               for k in ("GKP", "DEF", "MID", "FWD"))


def _breach(counts, rules):
    """How far a formation sits outside the rules, 0 when legal.

    Substitutions are accepted when they do not increase this. A real
    lineup is always legal and the test reduces to `must stay legal`, but
    if the API ever hands back something odd, a strict legality test would
    block every substitution and quietly report a score that ignores the
    bench - worse than a best effort.
    """
    return sum(max(0, rules[f"min_{k}"] - counts.get(k, 0))
               + max(0, counts.get(k, 0) - rules[f"max_{k}"])
               for k in ("GKP", "DEF", "MID", "FWD"))


def apply_subs(squad, rules):
    """Provisional auto-subs. squad: slot-ordered dicts already carrying
    `pos`, `played` and `settled` (his match is over).

    A starter who is settled with no minutes is replaced by the first
    bench player who did play and leaves the formation legal - a
    goalkeeper only ever by the reserve goalkeeper, as the game does.
    """
    start = [p for p in squad if p["slot"] <= rules["play"]]
    bench = [p for p in squad if p["slot"] > rules["play"]]
    out = [p for p in start if p["settled"] and not p["played"]]
    counts = {}
    for p in start:
        counts[p["pos"]] = counts.get(p["pos"], 0) + 1
    used = set()
    for gone in out:
        for cand in bench:
            if cand["id"] in used or not cand["played"]:
                continue
            if (gone["pos"] == "GKP") != (cand["pos"] == "GKP"):
                continue                      # keepers swap only for keepers
            trial = dict(counts)
            trial[gone["pos"]] = trial.get(gone["pos"], 0) - 1
            trial[cand["pos"]] = trial.get(cand["pos"], 0) + 1
            if _breach(trial, rules) > _breach(counts, rules):
                continue
            gone["subbed_out"] = True
            cand["subbed_in"] = True
            used.add(cand["id"])
            counts = trial
            break
    return squad


def _expected(pred_gw, gw):
    """The model's points for this gameweek, or its nearest live forecast.

    refresh.py zeroes the in-progress gameweek in predictions.json, so
    fall back to the first gameweek it still projects for that player.
    """
    if not pred_gw:
        return 0.0
    return next((v for v in pred_gw[max(0, gw - 1):] if v > 0), 0.0)


def build(data_dir, league_id, bootstrap, owned, id_of_code):
    """The whole weekly picture. Returns None when it cannot be built."""
    game = api.draft_game()
    gw = game.get("current_event")
    if not gw:
        return None
    rules = _rules(bootstrap)
    els = {e["id"]: e for e in bootstrap["elements"]}
    tshort = {t["id"]: t.get("short_name") or t["name"]
              for t in bootstrap["teams"]}
    det = api.league_details(league_id)

    try:
        fixtures = [f for f in api.fixtures() if f.get("event") == gw]
    except Exception:
        fixtures = []
    settled_teams, playing_teams = set(), set()
    for f in fixtures:
        for t in (f["team_h"], f["team_a"]):
            (settled_teams if f.get("finished") else playing_teams).add(t)
    settled_teams -= playing_teams          # a double gameweek still to come

    try:
        live = api.get(f"{api.DRAFT}/event/{gw}/live")["elements"]
        stats = {int(k): (v.get("stats") or {}) for k, v in live.items()}
    except Exception:
        stats = {}

    pred = {}
    try:
        pj = json.load(open(f"{data_dir}/predictions.json"))
        for p in pj.get("players", []):
            pid = id_of_code.get(p.get("code"))
            if pid:
                pred[pid] = p
    except Exception:
        pass

    entry_name, team_name, lentry = {}, {}, {}
    firsts = [le.get("player_first_name") for le in det.get("league_entries", [])]
    for le in det.get("league_entries", []):
        f = le.get("player_first_name") or le["entry_name"]
        if firsts.count(f) > 1 and le.get("player_last_name"):
            f = f + " " + le["player_last_name"][0]
        entry_name[le["entry_id"]] = f
        team_name[le["entry_id"]] = le.get("entry_name") or f
        lentry[le["id"]] = le["entry_id"]

    standings = {}
    for s in det.get("standings", []):
        ent = lentry.get(s["league_entry"])
        if ent is not None:
            standings[ent] = s

    managers = []
    for ent in entry_name:
        try:
            picks = (api.entry_event(ent, gw) or {}).get("picks") or []
        except Exception:
            picks = []
        squad = []
        for p in picks:
            pid = p.get("element") or p.get("id")
            if not pid:
                continue
            e = els.get(pid) or {}
            st = stats.get(pid, {})
            mins = int(st.get("minutes") or 0)
            team = e.get("team")
            pr = pred.get(pid) or {}
            squad.append(dict(
                id=pid, slot=p.get("position") or 99,
                name=e.get("web_name", str(pid)),
                pos=ETYPE.get(e.get("element_type"), "MID"),
                team=tshort.get(team, "?"),
                pts=int(st.get("total_points") or 0), mins=mins,
                played=mins > 0, settled=team in settled_teams,
                to_play=team not in settled_teams,
                status=e.get("status", "a"), news=(e.get("news") or "")[:70],
                avail=pr.get("avail"),
                ep_week=round(_expected(pr.get("gw"), gw), 2),
                ep_next=round((pr.get("gw") or [0])[gw] if pr.get("gw")
                              and len(pr["gw"]) > gw else 0.0, 2),
                rest=round(pr.get("rest", 0.0), 1),
                subbed_in=False, subbed_out=False))
        squad.sort(key=lambda x: x["slot"])
        apply_subs(squad, rules)
        counting = [p for p in squad
                    if (p["slot"] <= rules["play"] and not p["subbed_out"])
                    or p["subbed_in"]]
        raw = sum(p["pts"] for p in squad if p["slot"] <= rules["play"])
        livepts = sum(p["pts"] for p in counting)
        togo = [p for p in counting if p["to_play"]]
        proj = livepts + sum(p["ep_week"] for p in togo)
        s = standings.get(ent, {})
        managers.append(dict(
            entry=ent, name=entry_name[ent], team=team_name[ent],
            mine=ent == MY_ENTRY, live=livepts, raw=raw,
            subs=sum(1 for p in squad if p["subbed_in"]),
            to_play=len(togo), played=len(counting) - len(togo),
            proj=round(proj, 1), bench=sum(p["pts"] for p in squad
                                           if p not in counting),
            rank=s.get("rank"), total=s.get("total"),
            event_total=s.get("event_total", livepts), squad=squad))

    managers.sort(key=lambda m: -m["live"])
    for i, m in enumerate(managers):
        m["gw_rank"] = i + 1
    return dict(generated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
                gw=gw, finished=bool(game.get("current_event_finished")),
                all_played=not any(m["to_play"] for m in managers),
                managers=managers)


def write(data_dir, league_id, bootstrap, owned, id_of_code):
    """Write data/league.json. Returns the state, or None if unavailable."""
    state = build(data_dir, league_id, bootstrap, owned, id_of_code)
    if not state:
        return None
    json.dump(state, open(f"{data_dir}/league.json", "w"))
    return state

"""Every finished gameweek's squads, so a manager can look back.

The dashboards only ever knew about the gameweek the cron last scored.
That is the right thing for a live table and useless for "what did I put
out in week one" - which is exactly the question a six-man league argues
about on a Sunday.

A finished gameweek never changes, so this file is written once per
gameweek and then only appended to. The picks come from the draft API,
one call per manager per gameweek; the points come out of
player_stats.json, which already carries every player's match log, so no
second live call is needed and a rebuild of the whole season costs six
calls a week rather than six hundred.
"""
import json
import os

from . import api
from .league import LEAGUE_ID
from .weekly import _rules, apply_subs

ETYPE = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
KEEP = 38


def _load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def _by_id(stats):
    """element id -> {gw: (points, minutes)}, straight off the match log."""
    cols = stats.get("log_cols") or []
    try:
        gi, mi, pi = cols.index("gw"), cols.index("min"), cols.index("pts")
    except ValueError:
        return {}
    out = {}
    for p in (stats.get("players") or {}).values():
        pid = p.get("id")
        if pid is None:
            continue
        out[pid] = {int(r[gi]): (int(r[pi] or 0), int(r[mi] or 0))
                    for r in (p.get("log") or [])}
    return out


def build(data_dir, bootstrap=None, gw=None, fetch=None):
    """Add any finished gameweek the file does not already hold."""
    boot = bootstrap or _load(f"{data_dir}/draft_bootstrap.json") or {}
    stats = _load(f"{data_dir}/player_stats.json") or {}
    league = _load(f"{data_dir}/league.json") or {}
    out = _load(f"{data_dir}/gw_history.json") or {"gws": {}}
    out.setdefault("gws", {})

    current = gw if gw is not None else league.get("gw")
    if not current:
        return out

    els = {e["id"]: e for e in (boot.get("elements") or {}).values()} \
        if isinstance(boot.get("elements"), dict) else \
        {e["id"]: e for e in (boot.get("elements") or [])}
    tshort = {t["id"]: t["short_name"] for t in (boot.get("teams") or [])}
    rules = _rules(boot)
    log = _by_id(stats)
    get = fetch or api.entry_event

    det = api.league_details(LEAGUE_ID)
    les = [le for le in (det.get("league_entries") or []) if le.get("entry_id")]
    # two Bens in this league, so a first name alone does not identify one
    firsts = [le.get("player_first_name") for le in les]
    entries, names, teams = [], {}, {}
    for le in les:
        ent = le["entry_id"]
        entries.append(ent)
        f = le.get("player_first_name") or le.get("entry_name") or str(ent)
        if firsts.count(le.get("player_first_name")) > 1 and le.get("player_last_name"):
            f = f + " " + le["player_last_name"][0]
        names[ent] = f
        teams[ent] = le.get("entry_name") or f

    # a gameweek that has been scored never changes, so it is fetched once
    want = [g for g in range(1, int(current) + 1) if str(g) not in out["gws"]]
    for g in want:
        mgrs = {}
        for ent in entries:
            try:
                picks = (get(ent, g) or {}).get("picks") or []
            except Exception:
                picks = []
            if not picks:
                continue
            squad = []
            for p in picks:
                pid = p.get("element") or p.get("id")
                if not pid:
                    continue
                e = els.get(pid) or {}
                pts, mins = log.get(pid, {}).get(g, (0, 0))
                squad.append(dict(
                    id=pid, slot=p.get("position") or 99,
                    name=e.get("web_name", str(pid)),
                    pos=ETYPE.get(e.get("element_type"), "MID"),
                    team=tshort.get(e.get("team"), "?"),
                    pts=pts, mins=mins, played=mins > 0, settled=True,
                    subbed_in=False, subbed_out=False))
            if not squad:
                continue
            squad.sort(key=lambda x: x["slot"])
            apply_subs(squad, rules)
            xi = [p for p in squad
                  if (p["slot"] <= rules["play"] and not p["subbed_out"])
                  or p["subbed_in"]]
            mgrs[str(ent)] = dict(
                name=names.get(ent, str(ent)), team=teams.get(ent, ""),
                live=sum(p["pts"] for p in xi),
                bench=sum(p["pts"] for p in squad if p not in xi),
                subs=sum(1 for p in squad if p["subbed_in"]),
                squad=[{k: p[k] for k in
                        ("id", "slot", "name", "pos", "team", "pts", "mins",
                         "played", "subbed_in", "subbed_out")} for p in squad])
        if mgrs:
            out["gws"][str(g)] = dict(gw=g, managers=mgrs)

    for g, blk in out["gws"].items():
        ranked = sorted(blk["managers"].values(), key=lambda m: -m["live"])
        for i, m in enumerate(ranked, 1):
            m["gw_rank"] = i
    out["gws"] = {k: v for k, v in sorted(out["gws"].items(), key=lambda kv: int(kv[0]))[-KEEP:]}
    out["generated"] = stats.get("generated") or league.get("generated")
    return out


def write(data_dir, bootstrap=None, gw=None, fetch=None):
    out = build(data_dir, bootstrap, gw, fetch)
    with open(f"{data_dir}/gw_history.json", "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    return out

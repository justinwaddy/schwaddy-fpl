"""League news feed for the dashboard.

Runs inside refresh.py after predictions are written. Keeps a small state
block in data/news.json and emits an event whenever something changed
since the previous run: player flags (injury, doubt, suspension) and
recoveries, finished-gameweek scores (manager totals, standout player
scores, your own best scorers), standings overtakes, and processed
waivers or trades in the league.

Everything is diffed against the state from the last run, so the cron
cadence sets the news granularity (daily at 09:35 UK). The first run
seeds the state and only emits currently-flagged players who are owned
in the league, so the feed starts useful rather than empty or spammy.

data/news.json layout:
    {"state": {...}, "events": [{ts, gw, type, text, mine}, ...]}
Events are newest first, capped at MAX_EVENTS. The site renders them
directly; "mine" marks events that involve your squad or your entry.
"""
import json
import os
from datetime import datetime, timezone

from . import api
from .league import MY_ENTRY

MAX_EVENTS = 150
HIGH_SCORE = 12          # league-wide standout player score in a GW
MY_TOP_N = 3             # your best scorers listed after each GW

STATUS_WORD = {"i": "injured", "d": "a doubt", "s": "suspended",
               "u": "unavailable", "n": "not available"}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def _load(path):
    if os.path.exists(path):
        try:
            j = json.load(open(path))
            return j.get("state", {}), j.get("events", [])
        except Exception:
            pass
    return {}, []


def update(data_dir, league_id, bootstrap, owned, id_of_code):
    """owned: element id -> entry id. id_of_code: str(code) -> element id."""
    path = f"{data_dir}/news.json"
    state, events = _load(path)
    first_run = "flags" not in state
    ts = _now()
    new = []

    els = {e["id"]: e for e in bootstrap["elements"]}
    tshort = {t["id"]: t.get("short_name") or t["name"]
              for t in bootstrap["teams"]}

    det = api.league_details(league_id)
    game = api.draft_game()
    entry_name = {}          # entry id -> manager first name
    lentry_to_entry = {}     # league_entry id -> entry id
    firsts = [le.get("player_first_name") for le in det.get("league_entries", [])]
    for le in det.get("league_entries", []):
        f = le.get("player_first_name") or le["entry_name"]
        if firsts.count(f) > 1 and le.get("player_last_name"):
            f = f + " " + le["player_last_name"][0]
        entry_name[le["entry_id"]] = f
        lentry_to_entry[le["id"]] = le["entry_id"]

    def owner_tag(eid):
        ent = owned.get(eid)
        if ent == MY_ENTRY:
            return "yours", True
        if ent:
            return f"{entry_name.get(ent, ent)}'s", False
        return "free agent", False

    def pname(eid):
        e = els.get(eid)
        if not e:
            return f"player {eid}"
        return f"{e['web_name']} ({tshort.get(e['team'], '?')})"

    # ---- player flags: injuries, doubts, suspensions, recoveries ----
    prev_flags = state.get("flags", {})
    flags = {}
    for e in bootstrap["elements"]:
        key = str(e["code"])
        cur = [e["status"], (e.get("news") or "")[:90]]
        flags[key] = cur
        prev = prev_flags.get(key)
        flagged = e["status"] in STATUS_WORD
        tag, mine = owner_tag(e["id"])
        if first_run:
            if flagged and owned.get(e["id"]):
                new.append(dict(ts=ts, gw=None, type="injury", mine=mine,
                                text=f"{pname(e['id'])}: "
                                     f"{cur[1] or STATUS_WORD[e['status']]}"
                                     f" ({tag})"))
            continue
        if prev is None or prev == cur:
            continue
        if flagged:
            new.append(dict(ts=ts, gw=None, type="injury", mine=mine,
                            text=f"{pname(e['id'])}: "
                                 f"{cur[1] or STATUS_WORD[e['status']]}"
                                 f" ({tag})"))
        elif prev[0] in STATUS_WORD and e["status"] == "a":
            new.append(dict(ts=ts, gw=None, type="recovery", mine=mine,
                            text=f"{pname(e['id'])} cleared to play ({tag})"))
    state["flags"] = flags

    # ---- finished gameweek: manager totals, standout and my scorers ----
    cur_gw = game.get("current_event")
    finished = game.get("current_event_finished")
    if cur_gw and finished and state.get("scored_gw", 0) < cur_gw:
        rows = sorted(det.get("standings", []), key=lambda s: -s["event_total"])
        parts = []
        for s in rows:
            ent = lentry_to_entry.get(s["league_entry"])
            parts.append(f"{entry_name.get(ent, ent)} {s['event_total']}")
        if parts:
            new.append(dict(ts=ts, gw=cur_gw, type="score", mine=True,
                            text=f"GW{cur_gw} final: " + " · ".join(parts)))
        try:
            live = api.get(f"{api.DRAFT}/event/{cur_gw}/live")["elements"]
            pts = {int(k): v["stats"]["total_points"] for k, v in live.items()}
            highs = sorted((p for p in pts.items() if p[1] >= HIGH_SCORE),
                           key=lambda x: -x[1])[:8]
            for eid, p in highs:
                tag, mine = owner_tag(eid)
                new.append(dict(ts=ts, gw=cur_gw, type="haul", mine=mine,
                                text=f"{pname(eid)} hauled {p} pts in "
                                     f"GW{cur_gw} ({tag})"))
            mine_pts = sorted(((eid, p) for eid, p in pts.items()
                               if owned.get(eid) == MY_ENTRY),
                              key=lambda x: -x[1])[:MY_TOP_N]
            if mine_pts:
                best = ", ".join(f"{els[eid]['web_name']} {p}"
                                 for eid, p in mine_pts)
                new.append(dict(ts=ts, gw=cur_gw, type="score", mine=True,
                                text=f"Your GW{cur_gw} best: {best}"))
        except Exception:
            pass
        state["scored_gw"] = cur_gw

    # ---- standings overtakes ----
    prev_rank = state.get("ranks", {})
    ranks = {}
    total = {}
    for s in det.get("standings", []):
        ent = lentry_to_entry.get(s["league_entry"])
        if ent is None:
            continue
        ranks[str(ent)] = s["rank"]
        total[str(ent)] = s["total"]
    ORD = {1: "1st", 2: "2nd", 3: "3rd"}
    if prev_rank:
        for a in ranks:
            for b in ranks:
                if a == b or a not in prev_rank or b not in prev_rank:
                    continue
                if prev_rank[a] > prev_rank[b] and ranks[a] < ranks[b]:
                    mine = int(a) == MY_ENTRY or int(b) == MY_ENTRY
                    pos = ORD.get(ranks[a], f"{ranks[a]}th")
                    new.append(dict(ts=ts, gw=cur_gw, type="overtake",
                                    mine=mine,
                                    text=f"{entry_name.get(int(a), a)} "
                                         f"overtook "
                                         f"{entry_name.get(int(b), b)} "
                                         f"into {pos} "
                                         f"({total[a]} v {total[b]} pts)"))
    state["ranks"] = ranks

    # ---- processed waivers, free agent moves, trades ----
    seen = set(state.get("txn_seen", []))
    try:
        txns = api.get(f"{api.DRAFT}/draft/league/{league_id}"
                       f"/transactions").get("transactions", [])
    except Exception:
        txns = []
    KIND = {"w": "waiver", "f": "free agent"}
    for t in txns:
        tid = t.get("id")
        if tid in seen or t.get("result") not in (None, "a"):
            continue
        seen.add(tid)
        if first_run:
            continue
        ent = t.get("entry")
        mine = ent == MY_ENTRY
        new.append(dict(ts=ts, gw=t.get("event"), type="move", mine=mine,
                        text=f"{entry_name.get(ent, ent)} signed "
                             f"{pname(t.get('element_in'))}, dropped "
                             f"{pname(t.get('element_out'))} "
                             f"({KIND.get(t.get('kind'), 'move')})"))
    try:
        trades = api.get(f"{api.DRAFT}/draft/league/{league_id}"
                         f"/trades").get("trades", [])
    except Exception:
        trades = []
    for t in trades:
        tid = f"trade-{t.get('id')}"
        if tid in seen or t.get("state") not in ("a", "p", None):
            continue
        seen.add(tid)
        if first_run:
            continue
        oe = lentry_to_entry.get(t.get("offered_entry"))
        re_ = lentry_to_entry.get(t.get("received_entry"))
        mine = MY_ENTRY in (oe, re_)
        new.append(dict(ts=ts, gw=t.get("event"), type="move", mine=mine,
                        text=f"Trade: {entry_name.get(oe, oe)} and "
                             f"{entry_name.get(re_, re_)} completed a swap"))
    state["txn_seen"] = sorted(seen, key=str)

    events = (new + events)[:MAX_EVENTS]
    json.dump({"state": state, "events": events}, open(path, "w"))
    return len(new)

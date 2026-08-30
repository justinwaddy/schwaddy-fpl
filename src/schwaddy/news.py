"""League news feed for the dashboard.

Runs inside refresh.py and keeps a small state block in data/news.json,
emitting an event whenever something changed since the previous run.

Two cadences feed it:
  * the morning run (full model refresh) picks up overnight flag changes,
    processed waivers and trades, and the official end-of-gameweek recap;
  * the night run (refresh --news-only) fires after the day's matches have
    wrapped up and posts the matchday recap: the live table, who moved,
    a projected finish, and whose players are still to come.

Everything is diffed against the state from the last run, so the cron
cadence sets the news granularity. The first run seeds the state and only
emits currently-flagged players who are owned in the league, so the feed
starts useful rather than empty or spammy.

data/news.json layout:
    {"state": {...}, "events": [{ts, gw, type, text, mine}, ...]}
Events are newest first, capped at MAX_EVENTS. The site renders them
directly; "mine" marks events that involve your squad or your entry.
"""
import json
import os
from datetime import date, datetime, timezone

from . import api, liveform
from .league import MY_ENTRY

MAX_EVENTS = 220
HIGH_SCORE = 12          # league-wide standout player score in a GW
DAY_HIGH = 9             # standout free agent score in a single day
OWNED_HAUL = 10          # an owned player's score worth calling out
FLOP_MINS = 60           # played this long...
FLOP_PTS = 2             # ...and returned no more than this
MY_TOP_N = 3             # your best scorers listed after each GW
BENCH_REGRET = 10        # bench points worth pointing at
MAX_OVERTAKES = 3        # table churn to report per run
FREE_TAG = "free agent"  # single source for the unowned marker in text
WRAP_DAYS = {0, 4, 5, 6}  # Mon, Fri, Sat, Sun - the nights we wrap up

# A forward who plays an hour and returns nothing scores exactly 2 under
# these settings: long_play is 2 and he has added nothing to it. That is
# the crime worth mocking, so the test is "2 or fewer", not "under 2" -
# the literal reading would catch only brief cameos and miss every ghost
# who lasted the full ninety.
RUTHLESS = [
    "{p} led the line for {m} minutes and returned {n} pts ({tag}). "
    "The nets are still up, untouched",
    "{p}: {m} minutes, {n} pts ({tag}). Statistically indistinguishable "
    "from an empty shirt",
    "{p} spent {m} minutes offside for {n} pts ({tag}). The linesman got "
    "more touches",
    "{p} managed {n} pts in {m} minutes ({tag}). Strikers are paid to "
    "score. Somebody tell him",
    "{p}: {n} pts off {m} minutes ({tag}). The centre halves have never "
    "had an easier afternoon",
    "{p} completed {m} minutes and {n} pts ({tag}). A rumour in a shirt",
    "{p} gave {tag} {n} pts from {m} minutes. You could have started a "
    "bollard and saved the waiver",
    "{p}: {m} minutes, {n} pts ({tag}). Touched the ball, allegedly",
    "{p} returned {n} pts in {m} minutes ({tag}). Ghosted it, and not in "
    "the way that wins games",
    "{p} ran about for {m} minutes and brought back {n} pts ({tag}). "
    "Excellent cardio",
]
# said of anyone else who was on the pitch an hour and brought nothing back
FLOPS = [
    "{p} managed {n} pts in {m} minutes ({tag}) - a full shift, no wages",
    "{p}: {m} minutes, {n} pts ({tag}). Present, in the loosest sense",
    "{p} gave {tag} {n} pts off {m} minutes. Anonymous",
    "{p} played {m} minutes for {n} pts ({tag}) - he'll have seen it on telly",
    "{p}: {n} pts in {m} minutes ({tag}). Kept his shirt clean",
    "{p} clocked {m} minutes and {n} pts ({tag}). A quiet afternoon's work",
    "{p} returned {n} pts from {m} minutes ({tag}) - answers on a postcard",
]
# said of whoever is propping up the gameweek
PINTS = [
    "Pint watch: {who} props up GW{gw} on {n}. Mine's a Guinness",
    "Pint watch: {who} is bottom of GW{gw} with {n}. The bar is that way",
    "Pint watch: {who} brings up the rear on {n} in GW{gw}. Get them in",
    "Pint watch: {who} last in GW{gw}, {n} pts. A round is owed",
]

STATUS_WORD = {"i": "injured", "d": "a doubt", "s": "suspended",
               "u": "unavailable", "n": "not available"}
ORD = {1: "1st", 2: "2nd", 3: "3rd"}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def _ord(n):
    return ORD.get(n, f"{n}th")


def _load(path):
    if os.path.exists(path):
        try:
            j = json.load(open(path))
            return j.get("state", {}), j.get("events", [])
        except Exception:
            pass
    return {}, []


def _live(gw):
    """{element id: stats dict} for a gameweek, empty if unavailable."""
    try:
        els = api.get(f"{api.DRAFT}/event/{gw}/live")["elements"]
        return {int(k): v["stats"] for k, v in els.items()}
    except Exception:
        return {}


def _picks(entry_ids, gw):
    """{entry id: [(element id, squad position), ...]}. Positions 1-11 start."""
    out = {}
    for ent in entry_ids:
        try:
            r = api.entry_event(ent, gw)
        except Exception:
            continue
        out[ent] = [(p.get("element") or p.get("id"), p.get("position") or 99)
                    for p in (r.get("picks") or []) if p.get("element")
                    or p.get("id")]
    return out


def _swaps(prev_rank, cur_rank, names, totals=None, where=""):
    """One line per manager who gained places, naming everyone they passed.

    A manager jumping several spots passes several rivals at once, so the
    movers are collapsed rather than reported pair by pair.
    """
    out = []
    for a in cur_rank:
        if a not in prev_rank:
            continue
        passed = [b for b in cur_rank if b != a and b in prev_rank
                  and prev_rank[a] > prev_rank[b] and cur_rank[a] < cur_rank[b]]
        if not passed:
            continue
        passed.sort(key=lambda b: cur_rank[b])
        who = [str(names.get(int(b), b)) for b in passed]
        lst = who[0] if len(who) == 1 else ", ".join(who[:-1]) + " and " + who[-1]
        mine = int(a) == MY_ENTRY or any(int(b) == MY_ENTRY for b in passed)
        gap = f" ({totals[a]} pts)" if totals else ""
        out.append((mine, f"{names.get(int(a), a)} overtook {lst} into "
                          f"{_ord(cur_rank[a])}{where}{gap}"))
    out.sort(key=lambda x: not x[0])          # your own swaps first
    return out[:MAX_OVERTAKES]


def _expected(data_dir, id_of_code, gw):
    """{element id: expected points}, from the model's nearest live forecast.

    refresh.py zeroes the in-progress gameweek in predictions.json (its
    fixtures are already under way), so fall back to the first future
    gameweek the model still projects for that player.
    """
    out = {}
    try:
        pj = json.load(open(f"{data_dir}/predictions.json"))
    except Exception:
        return out
    for p in pj.get("players", []):
        pid = id_of_code.get(p.get("code"))
        if not pid:
            continue
        g = p.get("gw") or []
        out[pid] = next((v for v in g[max(0, gw - 1):] if v > 0), 0.0)
    return out


def update(data_dir, league_id, bootstrap, owned, id_of_code, weekly=None):
    """owned: element id -> entry id. id_of_code: str(code) -> element id.

    weekly: the state from weekly.build, when it is available. Its live
    table already carries provisional substitutions, so the feed quotes
    the same numbers the league tab shows.
    """
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

    def team_of(eid):
        return (els.get(eid) or {}).get("team")

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

    cur_gw = game.get("current_event")
    finished = game.get("current_event_finished")
    stats, pts = {}, {}          # live stats, filled in by the blocks below

    # ---- matchday recap: live table once the day's matches wrap up ----
    fx_ok = True
    try:
        fixtures = [f for f in api.fixtures() if f.get("event") == cur_gw]
    except Exception:
        fixtures, fx_ok = [], False
    done_now = sorted(f["id"] for f in fixtures if liveform.settled(f))
    seen_fx = set(state.get("fx_done", []))
    fresh_fx = [f for f in fixtures if liveform.settled(f)
                and f["id"] not in seen_fx]
    if cur_gw and fresh_fx and not finished:
        stats = _live(cur_gw)
        pts = {k: v.get("total_points", 0) for k, v in stats.items()}
        if weekly and weekly.get("managers"):
            # weekly.py has already applied provisional substitutions, which
            # the standings' event_total has not; use its table so the feed
            # and the league tab never disagree
            rows = [dict(ent=m["entry"], pts=m["live"], proj=m["proj"],
                         togo=[p["id"] for p in m["squad"] if p["to_play"]
                               and (p["slot"] <= 11 or p["subbed_in"])
                               and not p["subbed_out"]])
                    for m in weekly["managers"]]
        else:
            picks = _picks(list(entry_name), cur_gw)
            exp = _expected(data_dir, id_of_code, cur_gw)
            left_teams = {t for f in fixtures if not liveform.settled(f)
                          for t in (f["team_h"], f["team_a"])}
            live_tot = {}
            for s in det.get("standings", []):
                ent = lentry_to_entry.get(s["league_entry"])
                if ent is not None:
                    live_tot[ent] = s.get("event_total", 0)
            rows = []
            for ent in entry_name:
                xi = [pid for pid, pos in picks.get(ent, []) if pos <= 11]
                togo = [pid for pid in xi if team_of(pid) in left_teams]
                got = live_tot.get(ent)
                if got is None:
                    got = sum(pts.get(pid, 0) for pid in xi)
                rows.append(dict(ent=ent, pts=got, togo=togo,
                                 proj=got + sum(exp.get(p, 0.0) for p in togo)))
        rows.sort(key=lambda r: -r["pts"])
        all_played = not any(r["togo"] for r in rows)

        head = (f"GW{cur_gw} all played, bonus pending"
                if all_played else f"GW{cur_gw} so far")
        board = " · ".join(
            f"{entry_name[r['ent']]} {r['pts']}"
            + (f" ({len(r['togo'])} to play)" if r["togo"] else "")
            for r in rows)
        new.append(dict(ts=ts, gw=cur_gw, type="live", mine=True,
                        text=f"{head}: {board}"))

        # who moved in the live table since the last matchday
        cur_live = {str(r["ent"]): i + 1 for i, r in enumerate(rows)}
        for mine, txt in _swaps(state.get("live_rank", {}), cur_live,
                                entry_name, where=" in the live table"):
            new.append(dict(ts=ts, gw=cur_gw, type="overtake", mine=mine,
                            text=txt))
        state["live_rank"] = cur_live

        # projected finish from the model's forecast for who is left
        if not all_played and any(r["togo"] for r in rows):
            pr = sorted(rows, key=lambda r: -r["proj"])
            marg = pr[0]["proj"] - pr[1]["proj"] if len(pr) > 1 else 0
            call = ("too close to call" if marg < 3 else
                    f"{entry_name[pr[0]['ent']]} favourite by {marg:.0f}")
            new.append(dict(ts=ts, gw=cur_gw, type="projection", mine=True,
                            text=f"Projected GW{cur_gw}: "
                                 + " · ".join(f"{entry_name[r['ent']]} "
                                              f"{r['proj']:.0f}" for r in pr)
                                 + f" — {call}"))

        # whose players are still to come
        mine_togo = next((r["togo"] for r in rows if r["ent"] == MY_ENTRY), [])
        if mine_togo:
            new.append(dict(ts=ts, gw=cur_gw, type="squad", mine=True,
                            text=f"Your GW{cur_gw} still to play: "
                                 + ", ".join(pname(p) for p in mine_togo)))
        rest = [f"{entry_name[r['ent']]} {len(r['togo'])}" for r in rows
                if r["ent"] != MY_ENTRY and r["togo"]]
        if rest:
            new.append(dict(ts=ts, gw=cur_gw, type="squad", mine=False,
                            text="Still to play — " + ", ".join(rest)))

        # standouts, lowlights and waiver bait from today's matches only
        today_teams = {t for f in fresh_fx for t in (f["team_h"], f["team_a"])}
        today = [(pid, p) for pid, p in pts.items()
                 if team_of(pid) in today_teams]
        for pid, p in sorted((x for x in today if x[1] >= OWNED_HAUL
                              and owned.get(x[0])), key=lambda x: -x[1])[:6]:
            tag, mine = owner_tag(pid)
            new.append(dict(ts=ts, gw=cur_gw, type="haul", mine=mine,
                            text=f"{pname(pid)} hauled {p} pts today ({tag})"))
        # an hour on the pitch and nothing to show for it
        import random as _rnd
        flops = [(pid, p) for pid, p in today if owned.get(pid)
                 and p <= FLOP_PTS
                 and int((stats.get(pid) or {}).get("minutes") or 0) >= FLOP_MINS]
        def _fwd(pid):
            return (els.get(pid) or {}).get("element_type") == 4
        # forwards first: a striker who did not threaten is the better story,
        # and keepers and defenders carry the goals-conceded penalty, so an
        # unweighted list fills up with them every week
        flops.sort(key=lambda x: (not _fwd(x[0]), x[1]))
        for pid, p in flops[:5]:
            tag, mine = owner_tag(pid)
            mins = int((stats.get(pid) or {}).get("minutes") or 0)
            lines = RUTHLESS if _fwd(pid) else FLOPS
            line = lines[(pid + cur_gw) % len(lines)]     # stable per player
            new.append(dict(ts=ts, gw=cur_gw, type="flop", mine=mine,
                            text=line.format(p=pname(pid), n=p, m=mins,
                                             tag=tag)))
        for pid, _ in today:
            if not owned.get(pid):
                continue
            st = stats.get(pid, {})
            bits = []
            if st.get("red_cards"):
                bits.append("was sent off")
            if st.get("own_goals"):
                bits.append("put one in his own net")
            if st.get("penalties_missed"):
                bits.append("missed a penalty")
            if bits:
                tag, mine = owner_tag(pid)
                new.append(dict(ts=ts, gw=cur_gw, type="lowlight", mine=mine,
                                text=f"{pname(pid)} "
                                     f"{' and '.join(bits)} ({tag})"))
        for pid, p in sorted((x for x in today if x[1] >= DAY_HIGH
                              and not owned.get(x[0])),
                             key=lambda x: -x[1])[:3]:
            new.append(dict(ts=ts, gw=cur_gw, type="freeagent", mine=False,
                            text=f"Free agent watch: {pname(pid)} scored "
                                 f"{p} pts today and is unowned"))
    # ---- Friday to Monday night wrap-up ----
    # keyed off the last finished fixture's own date rather than the clock,
    # so a run that lands hours late still files under the right night and
    # never wraps the same evening twice
    try:
        done_fx = [f for f in fixtures if liveform.settled(f)
                   and f.get("kickoff_time")]
        last_day = max(f["kickoff_time"][:10] for f in done_fx) if done_fx else None
    except Exception:
        last_day = None
    wrapped = state.get("wrapped", [])
    if last_day and last_day not in wrapped:
        day = date.fromisoformat(last_day)
        if day.weekday() in WRAP_DAYS and weekly and weekly.get("managers"):
            mgrs = sorted(weekly["managers"], key=lambda m: -m["live"])
            night = day.strftime("%A")
            board = " · ".join(f"{m['name']} {m['live']}"
                               + (f" ({m['to_play']} left)" if m["to_play"] else "")
                               for m in mgrs)
            left = sum(m["to_play"] for m in mgrs)
            tail = (f"{left} players still to come" if left
                    else "everyone has played")
            new.append(dict(ts=ts, gw=cur_gw, type="wrap", mine=True,
                            text=f"{night} night, GW{cur_gw}: {board} - {tail}"))
            # who moved since the previous wrap
            cur = {str(m["entry"]): i + 1 for i, m in enumerate(mgrs)}
            for mine, txt in _swaps(state.get("wrap_rank", {}), cur,
                                    entry_name, where=" since last night"):
                new.append(dict(ts=ts, gw=cur_gw, type="wrap", mine=mine,
                                text=txt))
            state["wrap_rank"] = cur
            # best and worst of the night among players anyone owns
            day_ids = {p["id"] for m in mgrs for p in m["squad"]}
            scored = [(pid, pts[pid]) for pid in day_ids if pid in pts]
            if scored:
                best = max(scored, key=lambda x: x[1])
                if best[1] >= OWNED_HAUL:
                    tag, mine = owner_tag(best[0])
                    new.append(dict(ts=ts, gw=cur_gw, type="wrap", mine=mine,
                                    text=f"Man of the night: {pname(best[0])} "
                                         f"on {best[1]} ({tag})"))
            if not left:                       # the week is done, so is someone
                last = mgrs[-1]
                line = PINTS[day.toordinal() % len(PINTS)]
                new.append(dict(ts=ts, gw=cur_gw, type="pint",
                                mine=last["mine"],
                                text=line.format(who=last["name"], gw=cur_gw,
                                                 n=last["live"])))
            state["wrapped"] = (wrapped + [last_day])[-30:]

    if fx_ok:                    # a failed lookup must not re-open old matches
        state["fx_done"] = done_now

    # ---- finished gameweek: manager totals, standout and my scorers ----
    if cur_gw and finished and state.get("scored_gw", 0) < cur_gw:
        rows = sorted(det.get("standings", []), key=lambda s: -s["event_total"])
        parts = []
        for s in rows:
            ent = lentry_to_entry.get(s["league_entry"])
            parts.append(f"{entry_name.get(ent, ent)} {s['event_total']}")
        if parts:
            new.append(dict(ts=ts, gw=cur_gw, type="score", mine=True,
                            text=f"GW{cur_gw} final: " + " · ".join(parts)))
        if rows:
            win = lentry_to_entry.get(rows[0]["league_entry"])
            wins = state.get("gw_wins", {})
            wins[str(win)] = wins.get(str(win), 0) + 1
            state["gw_wins"] = wins
            n = wins[str(win)]
            new.append(dict(ts=ts, gw=cur_gw, type="score",
                            mine=win == MY_ENTRY,
                            text=f"{entry_name.get(win, win)} takes GW{cur_gw}"
                                 f" with {rows[0]['event_total']}"
                                 f" — {n} gameweek "
                                 f"{'win' if n == 1 else 'wins'} this season"))
        stats = _live(cur_gw)
        pts = {k: v.get("total_points", 0) for k, v in stats.items()}
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
                             for eid, p in mine_pts if eid in els)
            new.append(dict(ts=ts, gw=cur_gw, type="score", mine=True,
                            text=f"Your GW{cur_gw} best: {best}"))
        # points left sitting on the bench
        for ent, sq in _picks(list(entry_name), cur_gw).items():
            bp = sum(pts.get(pid, 0) for pid, pos in sq if pos > 11)
            if bp >= BENCH_REGRET:
                new.append(dict(ts=ts, gw=cur_gw, type="bench",
                                mine=ent == MY_ENTRY,
                                text=f"{entry_name.get(ent, ent)} left {bp} "
                                     f"pts on the bench in GW{cur_gw}"))
        state["scored_gw"] = cur_gw
        state["live_rank"] = {}

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
    if prev_rank:
        for mine, txt in _swaps(prev_rank, ranks, entry_name, total):
            new.append(dict(ts=ts, gw=cur_gw, type="overtake", mine=mine,
                            text=txt))
        if ranks != prev_rank and len(ranks) > 1:
            order = sorted(ranks, key=lambda e: ranks[e])
            lead, second = order[0], order[1]
            gap = total[lead] - total[second]
            new.append(dict(ts=ts, gw=cur_gw, type="race",
                            mine=MY_ENTRY in (int(lead), int(second)),
                            text=f"{entry_name.get(int(lead), lead)} leads by "
                                 f"{gap} from "
                                 f"{entry_name.get(int(second), second)}"
                            if gap else
                                 f"{entry_name.get(int(lead), lead)} and "
                                 f"{entry_name.get(int(second), second)} are "
                                 f"level at the top"))
    state["ranks"] = ranks

    # ---- processed waivers, free agent moves, trades ----
    seen = set(state.get("txn_seen", []))
    txn_ok = True
    try:
        txns = api.get(f"{api.DRAFT}/draft/league/{league_id}"
                       f"/transactions").get("transactions", [])
    except Exception:
        txns, txn_ok = [], False
    KIND = {"w": "waiver", "f": "free agent"}
    for t in txns:
        tid = t.get("id")
        if tid in seen or t.get("result") not in (None, "a"):
            continue
        seen.add(tid)
        if first_run or not state.get("txn_ready"):
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
        trades, txn_ok = [], False
    for t in trades:
        tid = f"trade-{t.get('id')}"
        if tid in seen or t.get("state") not in ("a", "p", None):
            continue
        seen.add(tid)
        if first_run or not state.get("txn_ready"):
            continue
        oe = lentry_to_entry.get(t.get("offered_entry"))
        re_ = lentry_to_entry.get(t.get("received_entry"))
        mine = MY_ENTRY in (oe, re_)
        new.append(dict(ts=ts, gw=t.get("event"), type="move", mine=mine,
                        text=f"Trade: {entry_name.get(oe, oe)} and "
                             f"{entry_name.get(re_, re_)} completed a swap"))
    # only start reporting moves once a run has actually reached the endpoint,
    # so a failure at seed time cannot dump the whole backlog as news later
    if txn_ok:
        state["txn_ready"] = True
    state["txn_seen"] = sorted(seen, key=str)

    # scope drives the news tab's filter: "mine" is your squad or entry,
    # "free" is chatter about players nobody owns, "league" is the rest
    for e in new:
        e["scope"] = ("mine" if e.get("mine") else
                      "free" if (e.get("type") == "freeagent"
                                 or f"({FREE_TAG})" in e.get("text", ""))
                      else "league")
    events = (new + events)[:MAX_EVENTS]
    json.dump({"state": state, "events": events}, open(path, "w"))
    return len(new)

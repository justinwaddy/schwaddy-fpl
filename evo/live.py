"""Run the trained network on the live season and write a plan.

This writes data/evo_plan.json and prints it. It does not touch anything
the existing pipeline owns: the dashboard, the projections and the claims
file are all still the matrix-completion model's. The network's plan sits
beside them so the two can be compared over a season before either is
trusted with the squad.

Three decisions come out of it:

  xi        the eleven and the bench order for the coming gameweek
  claims    ranked waiver claims, add and drop, for the waiver window
  board     the draft board, which only matters in August but is what the
            draft head was trained for

Injuries are no longer bolted on here. evo/injuries.py harvests the
game's own status history out of the archive repo's git log, so the
availability inside every feature and every baseline already carries them
- in training exactly as here - and applying the flags a second time
after scoring would double-count them. What this module still does is
keep that log current: the harvest stops where the archive repo last
committed, and one call to injuries.append_bootstrap brings it to today.

A player the league has locked (a new registration inside the 24-hour
lock) is still dropped from the claim list, which is a rule rather than a
projection.
"""
import json
import os
import sys
import time
import numpy as np

from .config import SEASONS, LIVE_SEASON, POSITIONS, SQUAD
from . import injuries
from .features import SeasonData, build_season, Standardizer
from .net import Brain
from .sim import SeasonView, _draft_ctx, _rank_swaps

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from schwaddy.lineup import pick_xi                       # noqa: E402
from schwaddy.league import LEAGUE_ID, OWNER_ID  # noqa: E402

def _ts(s):
    return float(np.datetime64(s.replace("Z", ""), "s").astype("int64"))


def load_live(cfg, boot, fixtures, prev=None):
    """SeasonData + features for the live season, on the live clock."""
    prev = prev or SeasonData(SEASONS[-1], cfg)
    ptype = {t["id"]: t["singular_name_short"] for t in boot["element_types"]}
    roster = {}
    for e in boot["elements"]:
        pos = ptype.get(e["element_type"])
        if pos not in POSITIONS:
            continue
        roster[int(e["code"])] = dict(pos=pos, team=int(e["team"]),
                                       added_ts=_ts(e["added"])
                                       if e.get("added") else None)
    sd = SeasonData(LIVE_SEASON, cfg, prev=prev, extra_fixtures=fixtures,
                    roster=roster)
    dl = {}
    for ev in boot["events"]["data"]:
        w = ev.get("waivers_time")
        dl[int(ev["id"])] = (_ts(ev["deadline_time"]),
                             _ts(w) if w else None)
    sd.set_deadlines(dl)
    return sd, build_season(sd, cfg)


def _fetch(cfg, offline, league_id):
    """(bootstrap, fixtures, ownership). Offline reads the repo's files."""
    d = cfg.data_dir
    if offline:
        boot = json.load(open(f"{d}/draft_bootstrap.json"))
        fixtures = json.load(open(f"{d}/fixtures_2627.json"))
        own = {}
        try:
            lg = json.load(open(f"{d}/league.json"))
            for m in lg["managers"]:
                for p in m["squad"]:
                    own[int(p["id"])] = ("me" if m.get("mine")
                                         else str(m.get("entry")))
        except Exception as e:
            print(f"  (no league.json ownership: {e})")
        return boot, fixtures, own
    from schwaddy import api
    boot = api.draft_bootstrap()
    fixtures = api.fixtures()
    own = {}
    for r in api.element_status(league_id)["element_status"]:
        if r.get("owner"):
            own[int(r["element"])] = ("me" if int(r["owner"]) == OWNER_ID
                                      else str(r["owner"]))
    return boot, fixtures, own


def _standings(cfg, own, offline):
    """Season totals per manager, for the head's league context."""
    try:
        lg = json.load(open(f"{cfg.data_dir}/league.json"))
        t = sorted((float(m.get("total", 0)) for m in lg["managers"]),
                   reverse=True)
        mine = next(float(m.get("total", 0)) for m in lg["managers"]
                    if m.get("mine"))
        return [mine] + [x for x in t if x != mine][:5] or [0.0] * 6
    except Exception:
        return [0.0] * 6


def main(cfg, model_path, offline=False, gw=None, out_json="data/evo_plan.json",
         league_id=LEAGUE_ID, board_n=40):
    z = np.load(model_path, allow_pickle=False)
    std = Standardizer(z["mean"], z["sd"])
    genome = z["best"]
    brain = Brain(genome, cfg)

    boot, fixtures, own = _fetch(cfg, offline, league_id)
    # bring the injury log up to date BEFORE the features are built, so
    # that availability reflects this morning's team news and not the
    # archive repo's last commit
    # only when online: appending stamps "observed just now" on the
    # bootstrap, which is a claim a cached file cannot support
    if cfg.use_injuries and not offline:
        injuries.append_bootstrap(cfg.data_dir, boot)
    if cfg.use_injuries:
        seen = injuries.last_observation(cfg.data_dir, LIVE_SEASON)
        if seen and time.time() - seen > 3 * 86400:
            print(f"  WARNING: injury log last observed "
                  f"{(time.time() - seen) / 86400:.1f} days ago")
    sd, arrays = load_live(cfg, boot, fixtures)
    sv = SeasonView(LIVE_SEASON, arrays, std)

    if gw is None:
        gw = int(boot["events"].get("next") or boot["events"]["current"] or 1)
    gw = max(1, min(38, gw))

    code_of = {int(e["id"]): int(e["code"]) for e in boot["elements"]}
    el = {int(e["id"]): e for e in boot["elements"]}
    team_name = {int(t["id"]): t["short_name"] for t in boot["teams"]}
    row_of = sd.row_of

    def rows_for(ids):
        out = []
        for i in ids:
            r = row_of.get(code_of.get(int(i), -1))
            if r is not None:
                out.append((int(i), r))
        return out

    mine_ids = [i for i, o in own.items() if o == "me"]
    owned_ids = set(own)
    locked = {int(e["id"]) for e in boot["elements"]
              if e.get("status") == "u"}

    def describe(i, r, ep=None, base=None):
        e = el.get(int(i), {})
        return dict(id=int(i), code=int(e.get("code", 0)),
                    name=e.get("web_name", str(i)),
                    pos=POSITIONS[int(sv.pos[r])],
                    team=team_name.get(int(e.get("team", 0)), ""),
                    status=e.get("status", "a"), news=e.get("news", ""),
                    p_play=round(float(arrays["p_play"][r, gw - 1]), 3),
                    base=None if base is None else round(float(base), 2),
                    ep=None if ep is None else round(float(ep), 2))

    plan = dict(generated=time.strftime("%Y-%m-%dT%H:%M", time.gmtime()),
                gw=gw, model=os.path.abspath(model_path),
                league=league_id, offline=bool(offline))

    # ---------------------------------------------------- which window
    # A gameweek asks for three things at two moments. Until waivers
    # process, a day before the deadline, the only way to sign anybody is
    # a ranked claim. From then until the deadline every unowned player is
    # a free agent, first come first served, and the team sheet is due.
    ev = {int(e["id"]): e for e in boot["events"]["data"]}
    now = time.time()
    dl_ts = _ts(ev[gw]["deadline_time"]) if gw in ev else None
    wv_ts = (_ts(ev[gw]["waivers_time"])
             if gw in ev and ev[gw].get("waivers_time") else
             (dl_ts - 86400 if dl_ts else None))
    if wv_ts and now < wv_ts:
        window = "waiver"
    elif dl_ts and now < dl_ts:
        window = "free_agency"
    else:
        window = "locked"
    plan["window"] = window
    plan["waivers_time"] = ev.get(gw, {}).get("waivers_time")
    plan["deadline"] = ev.get(gw, {}).get("deadline_time")
    is_fa = window == "free_agency"

    # ------------------------------------------------------------- line-up
    # always on the deadline clock: a team sheet is submitted then, not a
    # day earlier when the waivers were written
    squad = rows_for(mine_ids)
    if squad:
        ids = [i for i, _ in squad]
        rows = np.array([r for _, r in squad])
        base = sv.base_ep1_dl[rows, gw - 1]
        ep = brain.score("lineup", sv.key_dl, sv.Xn_dl, rows, gw, base,
                         feats=sv.X_dl[rows, gw - 1])
        sq = [dict(name=k, pos=POSITIONS[int(sv.pos[r])], ep=float(e))
              for k, (r, e) in enumerate(zip(rows, ep))]
        xi, bench, form = pick_xi(sq)
        plan["formation"] = "-".join(str(x) for x in form)
        plan["xi"] = [describe(ids[p["name"]], rows[p["name"]],
                               ep[p["name"]], base[p["name"]]) for p in xi]
        plan["bench"] = [describe(ids[p["name"]], rows[p["name"]],
                                  ep[p["name"]], base[p["name"]])
                         for p in bench]
        plan["xi_ep"] = round(float(sum(p["ep"] for p in xi)), 1)
    else:
        plan["xi"] = []
        plan["note"] = "no squad found; run online or refresh data/league.json"

    # ------------------------------------------- claims, or free agents
    if squad:
        free = []
        for i, e in el.items():
            if i in owned_ids or i in locked:
                continue
            r = row_of.get(code_of.get(i, -1))
            if r is not None and sv.pool[r, gw - 1]:
                free.append((i, r))
        if free:
            # the league's real standings, so the head's context matches
            # what it saw in training
            totals = _standings(cfg, own, offline)
            pairs = _rank_swaps(brain, sv, cfg, [int(r) for r in rows],
                                np.array([r for _, r in free]), gw, totals,
                                0, is_fa=is_fa)
            id_of_row = {r: i for i, r in free}
            id_of_row.update({int(r): i for r, i in zip(rows, ids)})
            out = []
            for gain, add, drop in (pairs[:cfg.max_claims] if cfg.max_claims
                                    else pairs[:10]):
                out.append(dict(pos=POSITIONS[int(sv.pos[add])],
                                gain=round(gain, 2),
                                add=describe(id_of_row.get(add, -1), add),
                                drop=describe(id_of_row.get(drop, -1), drop)))
            plan["claims"] = out
            plan["claims_note"] = ("waivers are unlimited; these are the "
                                   "ranked claims above the margin, and "
                                   "the game processes them in this order")
            plan["claims_are"] = ("free agents, first come first served"
                                  if is_fa else
                                  "waiver claims, in submission order")

    # ---------------------------------------------------------- draft board
    pool = np.flatnonzero(sv.pool[:, 0])
    base = sv.base_season[pool]
    top = pool[np.argsort(-base)[:max(board_n * 3, cfg.draft_shortlist)]]
    ctx = _draft_ctx(sv, top, [], dict(SQUAD), 0, 6,
                     np.zeros(sv.n, bool), sv.pool[:, 0])
    s = brain.score("draft", sv.key, sv.Xn, top, 1, sv.base_season[top],
                    ctx=ctx, feats=sv.X[top, 0])
    id_of = {int(e["code"]): int(e["id"]) for e in boot["elements"]}
    order = np.argsort(-s)[:board_n]
    plan["board"] = [describe(id_of.get(int(sd.codes[top[k]]), -1), top[k],
                              s[k], sv.base_season[top[k]])
                     for k in order]

    os.makedirs(os.path.dirname(out_json) or ".", exist_ok=True)
    with open(out_json, "w") as fh:
        json.dump(plan, fh, indent=1)
    print(f"gameweek {gw}   {out_json}")
    if plan.get("xi"):
        print(f"  XI ({plan['formation']}, {plan['xi_ep']} projected)")
        for p in plan["xi"]:
            print(f"    {p['pos']:3} {p['name']:<18} {p['team']:<4} "
                  f"{p['ep']:5.2f}  (base {p['base']:.2f})")
        print("  bench: " + ", ".join(p["name"] for p in plan["bench"]))
    if plan.get("claims"):
        print(f"  {plan['claims_are']} (window: {plan['window']}, "
              f"waivers {plan.get('waivers_time')}, "
              f"deadline {plan.get('deadline')})")
    for c in plan.get("claims", []):
        print(f"    +{c['add']['name']:<16} -{c['drop']['name']:<16} "
              f"{c['pos']:3} gain {c['gain']:.2f}")
    print("  board: " + ", ".join(p["name"] for p in plan["board"][:12]))
    return 0

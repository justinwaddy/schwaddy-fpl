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

Two things happen outside the network, deliberately. Injuries and
suspensions come from the API's status flags and are applied as a
multiplier AFTER scoring, because the archive holds no history of them to
train on - a model cannot learn what it has never seen. And a player the
league has locked (a new registration inside the 24-hour lock) is dropped
from the claim list.
"""
import json
import os
import sys
import time
import numpy as np

from .config import Config, SEASONS, LIVE_SEASON, POSITIONS, SQUAD
from .features import SeasonData, build_features, Standardizer
from .net import Brain
from .sim import SeasonView, POS_ID, _waiver_ctx, _draft_ctx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from schwaddy.lineup import pick_xi                       # noqa: E402
from schwaddy.league import LEAGUE_ID, OWNER_ID, MANAGERS  # noqa: E402

STATUS_FACTOR = {"a": 1.0, "d": 0.90, "i": 0.65, "s": 0.65, "u": 0.02,
                 "n": 0.30}


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
        roster[int(e["code"])] = dict(pos=pos, team=int(e["team"]), reg_gw=1)
    sd = SeasonData(LIVE_SEASON, cfg, prev=prev, extra_fixtures=fixtures,
                    roster=roster)
    dl = {}
    for ev in boot["events"]["data"]:
        w = ev.get("waivers_time")
        dl[int(ev["id"])] = (_ts(ev["deadline_time"]),
                             _ts(w) if w else None)
    sd.set_deadlines(dl)
    return sd, build_features(sd, cfg)


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


def main(cfg, model_path, offline=False, gw=None, out_json="data/evo_plan.json",
         league_id=LEAGUE_ID, board_n=40):
    z = np.load(model_path, allow_pickle=False)
    std = Standardizer(z["mean"], z["sd"])
    genome = z["best"]
    brain = Brain(genome, cfg)

    boot, fixtures, own = _fetch(cfg, offline, league_id)
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

    def factor(i):
        e = el.get(int(i), {})
        f = STATUS_FACTOR.get(e.get("status", "a"), 1.0)
        c = e.get("chance_of_playing_next_round")
        if c is not None and e.get("status") in ("d", "i", "s"):
            f = float(c) / 100.0
        return f

    def describe(i, r, ep=None, base=None):
        e = el.get(int(i), {})
        return dict(id=int(i), name=e.get("web_name", str(i)),
                    pos=POSITIONS[int(sv.pos[r])],
                    team=team_name.get(int(e.get("team", 0)), ""),
                    status=e.get("status", "a"), news=e.get("news", ""),
                    p_play=round(float(arrays["p_play"][r, gw - 1]), 3),
                    base=None if base is None else round(float(base), 2),
                    ep=None if ep is None else round(float(ep), 2))

    plan = dict(generated=time.strftime("%Y-%m-%dT%H:%M", time.gmtime()),
                gw=gw, model=os.path.abspath(model_path),
                league=league_id, offline=bool(offline))

    # ------------------------------------------------------------- line-up
    squad = rows_for(mine_ids)
    if squad:
        ids = [i for i, _ in squad]
        rows = np.array([r for _, r in squad])
        base = sv.base_ep1[rows, gw - 1]
        ep = brain.score("lineup", sv.key, sv.Xn, rows, gw, base,
                         feats=sv.X[rows, gw - 1])
        ep = np.array([e * factor(i) for e, i in zip(ep, ids)])
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

    # -------------------------------------------------------------- waivers
    if squad:
        free = []
        for i, e in el.items():
            if i in owned_ids or i in locked:
                continue
            r = row_of.get(code_of.get(i, -1))
            if r is not None and sv.pool[r, gw - 1]:
                free.append((i, r))
        if free:
            fr = np.array([r for _, r in free])
            fb = sv.base_next5[fr, gw - 1]
            keep = np.argsort(-fb)[:cfg.waiver_shortlist]
            free = [free[k] for k in keep]
            fr = fr[keep]
            cand = np.concatenate([fr, rows])
            cids = [i for i, _ in free] + ids
            mine_mask = np.concatenate([np.zeros(len(fr)), np.ones(len(rows))])
            cpos = sv.pos[cand]
            cbase = sv.base_next5[cand, gw - 1]
            strength = np.zeros(4); n_at = np.zeros(4)
            for p in range(4):
                m = sv.pos[rows] == p
                strength[p] = sv.base_next5[rows[m], gw - 1].sum()
                n_at[p] = m.sum()
            ctx = _waiver_ctx(sv, cand, strength, n_at, 39 - gw, 2.5, 0.0,
                              mine_mask, cpos)
            val = brain.score("waiver", sv.key, sv.Xn, cand, gw, cbase,
                              ctx=ctx, feats=sv.X[cand, gw - 1])
            # an injured free agent is not worth claiming and an injured
            # squad player is not worth keeping, so the same flag applies
            # to both sides of the swap
            val = np.array([v * factor(i) for v, i in zip(val, cids)])
            unit = float(np.std(cbase)) or 1.0
            margin = max(0.0, brain.margin) * unit
            claims = []
            for p in range(4):
                fa = np.flatnonzero((cpos == p) & (mine_mask == 0))
                ow = np.flatnonzero((cpos == p) & (mine_mask == 1))
                if not len(fa) or not len(ow):
                    continue
                worst = ow[int(np.argmin(val[ow]))]
                for a in fa[np.argsort(-val[fa])[:cfg.max_claims]]:
                    gain = float(val[a] - val[worst])
                    if gain > margin:
                        claims.append(dict(
                            pos=POSITIONS[p], gain=round(gain, 2),
                            add=describe(cids[a], cand[a], val[a], cbase[a]),
                            drop=describe(cids[worst], cand[worst],
                                          val[worst], cbase[worst])))
            claims.sort(key=lambda c: -c["gain"])
            plan["claims"] = claims[:cfg.max_claims]
            plan["waiver_margin"] = round(margin, 2)

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
    for c in plan.get("claims", []):
        print(f"  claim  +{c['add']['name']:<16} -{c['drop']['name']:<16} "
              f"{c['pos']:3} gain {c['gain']:.2f}")
    print("  board: " + ", ".join(p["name"] for p in plan["board"][:12]))
    return 0

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
from .sim import SeasonView, _draft_ctx, _score_pairs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from schwaddy.lineup import pick_xi                       # noqa: E402
from schwaddy.league import LEAGUE_ID, OWNER_ID  # noqa: E402

def _ts(s):
    return float(np.datetime64(s.replace("Z", ""), "s").astype("int64"))


def _overlay_market(sd, cfg):
    """Today's classic price onto each player's latest row.

    The weekly archive rebuild only started carrying prices after this
    was found, so the rows the live features read from can be weeks
    stale. data/prices.json is written by every refresh from the classic
    game and is the price as of this morning; put it where the features
    will read it. Ownership and transfers are left as the last known
    value - stale but real - rather than invented.
    """
    try:
        pj = json.load(open(f"{cfg.data_dir}/prices.json"))
    except Exception:
        return
    cols = pj.get("cols") or []
    if "price" not in cols:
        return
    k = cols.index("price")
    n = 0
    for code, row in pj.get("players", {}).items():
        i = sd.row_of.get(int(code))
        if i is None or row[k] is None:
            continue
        if len(sd.p_value[i]):
            v = np.array(sd.p_value[i], dtype=float)
            v[-1] = float(row[k]) * 10.0
        else:
            # a signing with no archive row yet: his only price is today's,
            # and it is read as his opening one
            v = np.array([float(row[k]) * 10.0])
            sd.p_sel[i] = np.array([0.0])
        sd.p_value[i] = v
        n += 1
    if n:
        print(f"  market: today's price overlaid for {n} players")


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
    _overlay_market(sd, cfg)
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


def _check_target(z, cfg, model_path):
    """The checkpoint and this configuration must agree on what a point
    IS. A model trained on a target that included the defensive-
    contribution rule where the archive had it (every checkpoint before
    dc_target existed) has learnt defenders' values from four seasons
    without the rule and one with; run under a configuration that scores
    without it, its baselines and its residuals no longer describe the
    same quantity. Loud rather than fatal: the plan is still written, and
    the warning says to retrain."""
    try:
        mcfg = json.loads(str(z["cfg"])) if "cfg" in z.files else {}
    except Exception:
        mcfg = {}
    # before the flag existed the target carried the rule wherever the
    # column was present, which is dc_target=True in today's terms
    trained = bool(mcfg.get("dc_target", True))
    if trained != bool(cfg.dc_target):
        print(f"  WARNING: {model_path} was trained with dc_target="
              f"{trained} and this run scores with dc_target="
              f"{cfg.dc_target}; its values are on a different target. "
              f"Retrain (python -m evo.run cv / train) before trusting it.")


MAX_FALLBACKS = 3     # alternatives kept per change when several models vote


def _load_models(paths, cfg):
    """One (Brain, Standardizer) per checkpoint, each checked against the
    configuration's target. A checkpoint is the best genome plus the
    standardizer it was trained with, so each model reads the season
    through its own scaling."""
    out = []
    for p in paths:
        z = np.load(p, allow_pickle=False)
        _check_target(z, cfg, p)
        out.append((z["best"], Standardizer(z["mean"], z["sd"])))
    return out


def _borda(lists, min_votes):
    """Combine ranked lists from several models into one.

    Each list is [(key, gain), ...] in that model's own order. A key
    scores L - rank in every list it appears in, L being the longest
    list, so a candidate every model puts first beats one that a single
    model puts first and the rest leave out - which is the point: the
    failure seen in cross-validation was per-seed, and a vote across
    seeds is what removes the odd lineage. Keys listed by fewer than
    min_votes models are dropped. Returns [(key, mean gain, votes, borda)]
    in vote order, ties broken by mean gain.
    """
    L = max((len(l) for l in lists), default=0)
    score, gains = {}, {}
    for l in lists:
        for k, (key, g) in enumerate(l):
            score[key] = score.get(key, 0) + (L - k)
            gains.setdefault(key, []).append(float(g))
    out = [(key, float(np.mean(gains[key])), len(gains[key]), score[key])
           for key in score if len(gains[key]) >= min_votes]
    out.sort(key=lambda t: (-t[3], -t[1]))
    return out


def _vote_swaps(brains, views, cfg, squad, free_rows, gw, totals, is_fa,
                min_votes, max_fb, base_all=None):
    """The ranked list the models submit together for one window.

    Built as _rank_swaps builds it for one model, with the vote inside
    the loop: at each step every model scores every (free agent, squad
    player) pair against the squad the earlier steps leave, the pairs
    are merged by Borda count, the best-backed one is the change and the
    next few for the same drop are its fallbacks, and the change is
    ASSUMED before the next step. Merging the models' finished lists
    instead would put one man at the top of two changes and average
    gains that were scored against different squads. Free agency, or
    sequential_claims off, is one scoring and one merge, grouped by the
    man dropped. Two rules on top of the merge: a CHANGE needs a majority
    of the models agreeing that its man should be dropped (any pair
    listed for him counts; the replacement is then the best-backed pair
    for him, and a listed pair needs min_votes), so the list ends where
    most models would hold rather than where the last two still clear
    their margin; and a man added earlier in the list is never the drop
    of a later one, since "claim him, then drop him" is not advice. With one model the Borda order is the model's own, so
    this is _rank_swaps with the fallback count as a parameter.

    Returns [(gain, add, drop, votes, borda, change, fallback)].
    """
    majority = len(brains) // 2 + 1
    def merged(sq, free):
        """The Borda-merged pairs, and for each man dropped how many
        models list ANY swap for him. A manager decides who goes and
        then who comes in, and the vote is taken the same way: the
        models agreeing that a man should go is the majority that
        matters, since three of them can be sure of the drop and still
        name three different replacements, which as a vote on exact
        pairs reads as no change at all."""
        lists = [[((a, d), g) for g, a, d in
                  _score_pairs(b, v, cfg, sq, free, gw, totals, 0, is_fa,
                               base_all=base_all)]
                 for b, v in zip(brains, views)]
        drop_votes = {}
        for l in lists:
            for d in {key[1] for key, _ in l}:
                drop_votes[d] = drop_votes.get(d, 0) + 1
        return _borda(lists, min_votes), drop_votes

    out = []
    if is_fa or not cfg.sequential_claims:
        pairs, drop_votes = merged(list(squad), free_rows)
        by_drop, order = {}, []
        for (a, d), g, votes, borda in pairs:
            if d not in by_drop:
                if drop_votes.get(d, 0) < majority:
                    continue          # a drop nobody much backs
                by_drop[d] = []
                order.append(d)
            by_drop[d].append((g, a, votes, borda))
        for step, d in enumerate(order, 1):
            for k, (g, a, votes, borda) in enumerate(by_drop[d][:1 + max_fb]):
                out.append((g, a, d, votes, borda, step, k > 0))
        return out
    squad, taken, step = list(squad), set(), 0
    for _ in range(15):
        free = np.array([r for r in free_rows if r not in taken])
        if len(free) == 0:
            break
        pairs, drop_votes = merged(squad, free)
        pairs = [pr for pr in pairs if pr[0][1] not in taken]
        head = [pr for pr in pairs if drop_votes.get(pr[0][1], 0) >= majority]
        if not head:
            break
        (a, d), g, votes, borda = head[0]
        step += 1
        out.append((g, a, d, votes, borda, step, False))
        nfb = 0
        for (a2, d2), g2, v2, b2 in pairs[1:]:
            if d2 == d and a2 != a:
                out.append((g2, a2, d2, v2, b2, step, True))
                nfb += 1
                if nfb >= max_fb:
                    break
        squad.remove(d)
        squad.append(a)
        taken.add(a)
    return out


def main(cfg, model_path, offline=False, gw=None, out_json="data/evo_plan.json",
         league_id=LEAGUE_ID, board_n=40, horizon="next5"):
    """horizon: what a claim is judged on. "next5" is the five-gameweek
    total the policy was trained against; "rest" is the same head over
    the rest-of-season baseline (features.build_season's base_rest: the
    remaining fixtures at the same rate, the injury return date
    included), for a manager who wants to hold what he claims. The
    eleven is this week's either way."""
    if horizon not in ("next5", "rest"):
        raise ValueError(f"horizon must be next5 or rest, not {horizon!r}")
    # one checkpoint or several. With several, every decision is taken by
    # a vote: the eleven on the median expected points across models, the
    # claims and the board by Borda count over each model's own ranking.
    paths = [model_path] if isinstance(model_path, str) else list(model_path)
    models = _load_models(paths, cfg)
    n_models = len(models)
    if not cfg.dc_target and not cfg.dc_bonus:
        # the league scores WITH the rule. The target was scored without
        # it so that the folds are comparable; at pick time the rule's
        # measured per-appearance value goes back onto every defender
        # and midfielder in a DC-era season (see features.dc_bonus_ppa)
        from .config import Config
        cfg = Config(**{**cfg.to_dict(), "dc_bonus": True})
        from .features import dc_bonus_ppa
        ppa = dc_bonus_ppa(cfg.data_dir)
        print("  DC bonus at pick time, points per appearance: "
              + ", ".join(f"{p} {v:+.2f}" for p, v in zip(POSITIONS, ppa)))
    brains = [Brain(g, cfg) for g, _ in models]

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
    # a view per distinct standardizer: models trained on the same seasons
    # share one, and the transform is the only thing that differs
    views, seen_std = [], {}
    for _, std in models:
        k = (std.mean.tobytes(), std.sd.tobytes())
        if k not in seen_std:
            seen_std[k] = SeasonView(LIVE_SEASON, arrays, std)
        views.append(seen_std[k])
    sv = views[0]
    if n_models > 1:
        print(f"  ensemble of {n_models} models, "
              f"{len(seen_std)} distinct standardizer(s)")

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

    def describe(i, r, ep=None, base=None, votes=None):
        e = el.get(int(i), {})
        d = dict(id=int(i), code=int(e.get("code", 0)),
                 name=e.get("web_name", str(i)),
                 pos=POSITIONS[int(sv.pos[r])],
                 team=team_name.get(int(e.get("team", 0)), ""),
                 status=e.get("status", "a"), news=e.get("news", ""),
                 p_play=round(float(arrays["p_play"][r, gw - 1]), 3),
                 base=None if base is None else round(float(base), 2),
                 ep=None if ep is None else round(float(ep), 2))
        if votes is not None:
            d["votes"] = int(votes)
        return d

    plan = dict(generated=time.strftime("%Y-%m-%dT%H:%M", time.gmtime()),
                gw=gw, horizon=horizon, model=os.path.abspath(paths[0]),
                models=[os.path.abspath(p) for p in paths],
                n_models=n_models,
                vote=("median expected points for the eleven, Borda count "
                      "over each model's ranking for claims and the board"
                      if n_models > 1 else None),
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
        eps = np.array([b.score("lineup", v.key_dl, v.Xn_dl, rows, gw, base,
                                feats=v.X_dl[rows, gw - 1])
                        for b, v in zip(brains, views)])
        # the median, not the mean: one lineage with an odd scale on its
        # line-up head would otherwise move every man's number
        ep = np.median(eps, axis=0)
        # how many models would start each man in their own eleven
        starts = np.zeros(len(rows), int)
        for e_k in eps:
            sq_k = [dict(name=k, pos=POSITIONS[int(sv.pos[r])], ep=float(e))
                    for k, (r, e) in enumerate(zip(rows, e_k))]
            xi_k, _, _ = pick_xi(sq_k)
            for p in xi_k:
                starts[p["name"]] += 1
        sq = [dict(name=k, pos=POSITIONS[int(sv.pos[r])], ep=float(e))
              for k, (r, e) in enumerate(zip(rows, ep))]
        xi, bench, form = pick_xi(sq)
        plan["formation"] = "-".join(str(x) for x in form)
        vote = (lambda k: starts[k]) if n_models > 1 else (lambda k: None)
        plan["xi"] = [describe(ids[p["name"]], rows[p["name"]],
                               ep[p["name"]], base[p["name"]],
                               vote(p["name"])) for p in xi]
        plan["bench"] = [describe(ids[p["name"]], rows[p["name"]],
                                  ep[p["name"]], base[p["name"]],
                                  vote(p["name"]))
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
            free_rows = np.array([r for _, r in free])
            squad_rows = [int(r) for r in rows]
            # a pair one model alone lists is the tail the vote is there
            # to remove; with three or more models it takes two
            min_votes = 2 if n_models >= 3 else 1
            base_all = None
            if horizon == "rest":
                base_all = arrays["base_rest_dl" if is_fa else "base_rest"]
            pairs = _vote_swaps(brains, views, cfg, squad_rows, free_rows, gw,
                                totals, is_fa, min_votes, MAX_FALLBACKS,
                                base_all=base_all)
            if cfg.max_claims:
                pairs = pairs[:cfg.max_claims]
            id_of_row = {r: i for i, r in free}
            id_of_row.update({int(r): i for r, i in zip(rows, ids)})
            # the whole list, each row marked as a change or a fallback
            # for the change above it: with waivers unlimited the list is
            # long, and ten changes is a different thing from four changes
            # with two fallbacks each
            out = []
            for gain, add, drop, votes, borda, step, fb in pairs:
                row = dict(pos=POSITIONS[int(sv.pos[add])],
                           gain=round(gain, 2), change=step, fallback=fb,
                           add=describe(id_of_row.get(add, -1), add),
                           drop=describe(id_of_row.get(drop, -1), drop))
                if n_models > 1:
                    row["votes"] = int(votes)
                    row["borda"] = int(borda)
                out.append(row)
            plan["changes"] = max((o["change"] for o in out), default=0)
            plan["claims"] = out
            plan["claims_note"] = ("waivers are unlimited; these are the "
                                   "ranked claims above the margin, and "
                                   "the game processes them in this order"
                                   + ("; gains are expected points over "
                                      "the rest of the season, the injury "
                                      "return date included"
                                      if horizon == "rest" else
                                      "; gains are expected points over "
                                      "the next five gameweeks"))
            if n_models > 1:
                plan["min_votes"] = min_votes
                plan["majority"] = n_models // 2 + 1
                plan["claims_note"] += (
                    f"; voted across {n_models} models by Borda count at "
                    f"every step of the list, a change needing "
                    f"{plan['majority']} models agreeing on the man dropped "
                    f"and a listed pair {min_votes}, at most {MAX_FALLBACKS} "
                    f"fallbacks kept per change, and gain is the mean over "
                    f"the models that list it")
            plan["claims_are"] = ("free agents, first come first served"
                                  if is_fa else
                                  "waiver claims, in submission order")

    # ---------------------------------------------------------- draft board
    pool = np.flatnonzero(sv.pool[:, 0])
    base = sv.base_season[pool]
    top = pool[np.argsort(-base)[:max(board_n * 3, cfg.draft_shortlist)]]
    ctx = _draft_ctx(sv, top, [], dict(SQUAD), 0, 6,
                     np.zeros(sv.n, bool), sv.pool[:, 0])
    S = np.array([b.score("draft", v.key, v.Xn, top, 1, sv.base_season[top],
                          ctx=ctx, feats=v.X[top, 0])
                  for b, v in zip(brains, views)])
    s = S.mean(axis=0)
    # rank within each model, then the mean rank: one model's outlier
    # score cannot carry a name onto the board on its own
    ranks = np.argsort(np.argsort(-S, axis=1), axis=1).mean(axis=0)
    id_of = {int(e["code"]): int(e["id"]) for e in boot["elements"]}
    order = np.lexsort((-s, ranks))[:board_n]
    plan["board"] = [describe(id_of.get(int(sd.codes[top[k]]), -1), top[k],
                              s[k], sv.base_season[top[k]])
                     for k in order]

    os.makedirs(os.path.dirname(out_json) or ".", exist_ok=True)
    with open(out_json, "w") as fh:
        json.dump(plan, fh, indent=1)
    print(f"gameweek {gw}   {out_json}   (claims judged on "
          f"{'the rest of the season' if horizon == 'rest' else 'the next five gameweeks'})")
    if plan.get("xi"):
        print(f"  XI ({plan['formation']}, {plan['xi_ep']} projected)")
        for p in plan["xi"]:
            v = (f"  {p['votes']}/{n_models} start him"
                 if n_models > 1 else "")
            print(f"    {p['pos']:3} {p['name']:<18} {p['team']:<4} "
                  f"{p['ep']:5.2f}  (base {p['base']:.2f}){v}")
        print("  bench: " + ", ".join(
            p["name"] + (f" ({p['votes']}/{n_models})" if n_models > 1 else "")
            for p in plan["bench"]))
    if plan.get("claims"):
        print(f"  {plan['claims_are']} (window: {plan['window']}, "
              f"waivers {plan.get('waivers_time')}, "
              f"deadline {plan.get('deadline')})")
    for c in plan.get("claims", []):
        tag = "  fallback" if c.get("fallback") else f"change {c.get('change', '')}"
        v = f"  {c['votes']}/{n_models} models" if n_models > 1 else ""
        print(f"    {tag:10} +{c['add']['name']:<16} -{c['drop']['name']:<16} "
              f"{c['pos']:3} gain {c['gain']:.2f}{v}")
    print("  board: " + ", ".join(p["name"] for p in plan["board"][:12]))
    return 0

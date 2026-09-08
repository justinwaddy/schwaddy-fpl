"""One simulated season of a six-manager FPL Draft league.

The mechanics are the game's, not a convenient approximation of it:

  Draft     snake over 15 rounds, quota 2/5/5/3, forced fill once the
            remaining picks equal the remaining positional needs.
  Waivers   from cfg.waiver_first_gw, decided at the waiver deadline (a
            day before the gameweek deadline), same-position swaps only
            so the quota always holds. Each manager submits up to
            cfg.max_claims ranked claims; they are processed in reverse
            standings order and a manager may win cfg.max_success_per_gw
            of them.
  Line-up   11 starters, exactly 1 GKP, 3-5 DEF, 2-5 MID, 1-3 FWD, no
            captain, chosen by schwaddy.lineup.pick_xi so that the rules
            here and the rules on the dashboard cannot drift apart.
  Subs      the game's automatic substitutions, applied with realized
            minutes: a starter who did not play is replaced by the first
            legal bench player who did.

Only the reward is realized: every score a manager acts on comes from the
feature tensor, which was built strictly before the decision.
"""
import os
import sys
import numpy as np

from .config import SQUAD, SQUAD_SIZE, POSITIONS, N_MANAGERS
from .net import CONTEXT

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from schwaddy.lineup import pick_xi                # noqa: E402
from schwaddy.draftsim import autosub              # noqa: E402

POS_ID = {p: i for i, p in enumerate(POSITIONS)}


class SeasonView:
    """The arrays one season contributes, plus its standardized copy."""

    def __init__(self, key, arrays, standardizer):
        self.key = key
        self.a = arrays
        self.Xn = standardizer.transform(arrays["X"])
        self.X = arrays["X"]
        self.pos = arrays["pos"].astype(int)
        self.n = self.X.shape[0]
        self.real = arrays["real"]
        self.played = arrays["minutes"] > 0
        self.pool = arrays["pool"]
        self.base_season = arrays["base_season"]
        self.base_next5 = arrays["base_next5"]
        self.base_ep1 = arrays["base_ep1"]


def _need_positions(need, left):
    """Positions a manager may still take, with forced fill."""
    tot = sum(need.values())
    if tot == left:                       # every remaining pick is spoken for
        return {p for p, v in need.items() if v > 0}
    return {p for p, v in need.items() if v > 0}


def run_draft(brains, sv, cfg, rng, order):
    """Snake draft. Returns squads as lists of player rows."""
    n = sv.n
    squads = [[] for _ in range(N_MANAGERS)]
    need = [dict(SQUAD) for _ in range(N_MANAGERS)]
    owned = np.zeros(n, bool)
    avail0 = sv.pool[:, 0].copy()

    seq = []
    for r in range(SQUAD_SIZE):
        seq += order if r % 2 == 0 else order[::-1]

    # scarcity: how far the board falls at each position between now and
    # this manager's next pick, on the shared heuristic board
    for pick_no, m in enumerate(seq):
        rnd = pick_no // N_MANAGERS
        left = SQUAD_SIZE - len(squads[m])
        allowed = _need_positions(need[m], left)
        cand = np.flatnonzero(avail0 & ~owned)
        if len(cand) == 0:
            break
        cand = cand[np.isin(sv.pos[cand], [POS_ID[p] for p in allowed])]
        if len(cand) == 0:
            cand = np.flatnonzero(avail0 & ~owned)
        base = sv.base_season[cand]
        if len(cand) > cfg.draft_shortlist:
            top = np.argpartition(-base, cfg.draft_shortlist)[:cfg.draft_shortlist]
            cand, base = cand[top], base[top]

        try:
            nxt = seq.index(m, pick_no + 1)
            gap = nxt - pick_no
        except ValueError:
            gap = 0
        ctx = _draft_ctx(sv, cand, squads[m], need[m], rnd, gap, owned, avail0)
        s = brains[m].score("draft", sv.key, sv.Xn, cand, 1, base, ctx=ctx,
                            feats=sv.X[cand, 0])
        pick = int(cand[int(np.argmax(s))])
        squads[m].append(pick)
        owned[pick] = True
        need[m][POSITIONS[sv.pos[pick]]] -= 1
    return squads, owned


def _draft_ctx(sv, cand, squad, need, rnd, gap, owned, avail0):
    """Per-candidate draft context (see net.C_DRAFT)."""
    k = len(cand)
    ctx = np.zeros((k, CONTEXT["draft"]), np.float32)
    ctx[:, 0] = rnd / 15.0
    ctx[:, 1] = min(gap, 12) / 12.0
    cpos = sv.pos[cand]
    ctx[:, 2] = np.array([need[POSITIONS[p]] for p in cpos]) / 5.0
    ctx[:, 3] = sum(need.values()) / 15.0
    strength = np.zeros(4)
    for i in squad:
        strength[sv.pos[i]] += sv.base_season[i]
    ctx[:, 4:8] = strength / 200.0
    # scarcity at each position: best available now minus the best that
    # will plausibly survive until this manager picks again
    scar = np.zeros(4)
    free = np.flatnonzero(avail0 & ~owned)
    for p in range(4):
        fp = free[sv.pos[free] == p]
        if len(fp) == 0:
            continue
        v = np.sort(sv.base_season[fp])[::-1]
        scar[p] = (v[0] - v[min(gap, len(v) - 1)]) / 50.0
    ctx[:, 8] = scar[cpos]
    ctx[:, 9] = (15 - sum(need.values())) / 15.0
    return ctx


def _waiver_ctx(sv, cand, squad_pos_strength, n_at_pos, gws_left, rank,
                gap, mine_mask, cpos):
    k = len(cand)
    ctx = np.zeros((k, CONTEXT["waiver"]), np.float32)
    ctx[:, 0] = gws_left / 38.0
    ctx[:, 1] = squad_pos_strength[cpos] / 20.0
    ctx[:, 2] = n_at_pos[cpos] / 5.0
    ctx[:, 3] = rank / 5.0
    ctx[:, 4] = np.tanh(gap / 100.0)
    ctx[:, 5] = mine_mask
    return ctx


def run_waivers(brains, sv, cfg, squads, owned, gw, totals, priority):
    """One waiver window. Mutates squads/owned; returns the moves made."""
    n = sv.n
    free = sv.pool[:, gw - 1] & ~owned
    free_rows = np.flatnonzero(free)
    if len(free_rows) == 0:
        return []
    gws_left = 39 - gw
    claims = {}
    for m in range(N_MANAGERS):
        sq = np.array(squads[m])
        spos = sv.pos[sq]
        strength = np.zeros(4)
        n_at = np.zeros(4)
        for p in range(4):
            strength[p] = sv.base_next5[sq[spos == p], gw - 1].sum()
            n_at[p] = (spos == p).sum()
        rank = float(sorted(totals, reverse=True).index(totals[m]))
        gap = totals[m] - max(totals)

        # shortlist the free agents on the shared heuristic board, then
        # let the head reorder them: scoring six hundred free agents with
        # every genome every week is the whole cost of the simulation
        fb = sv.base_next5[free_rows, gw - 1]
        pick = free_rows
        if len(free_rows) > cfg.waiver_shortlist:
            top = np.argpartition(-fb, cfg.waiver_shortlist)[:cfg.waiver_shortlist]
            pick = free_rows[top]
        cand = np.concatenate([pick, sq])
        mine = np.concatenate([np.zeros(len(pick)), np.ones(len(sq))])
        cpos = sv.pos[cand]
        base = sv.base_next5[cand, gw - 1]
        ctx = _waiver_ctx(sv, cand, strength, n_at, gws_left, rank, gap,
                          mine, cpos)
        val = brains[m].score("waiver", sv.key, sv.Xn, cand, gw, base,
                              ctx=ctx, feats=sv.X[cand, gw - 1])
        unit = float(np.std(base)) or 1.0
        margin = max(0.0, brains[m].margin) * unit
        pairs = []
        for p in range(4):
            fa = np.flatnonzero((cpos == p) & (mine == 0))
            ow = np.flatnonzero((cpos == p) & (mine == 1))
            if len(fa) == 0 or len(ow) == 0:
                continue
            worst = ow[int(np.argmin(val[ow]))]
            for a in fa[np.argsort(-val[fa])[:cfg.max_claims]]:
                gain = float(val[a] - val[worst])
                if gain > margin:
                    pairs.append((gain, int(cand[a]), int(cand[worst])))
        pairs.sort(key=lambda x: -x[0])
        claims[m] = pairs[:cfg.max_claims]

    moves = []
    for m in priority:
        wins = 0
        for gain, add, drop in claims[m]:
            if wins >= cfg.max_success_per_gw:
                break
            if owned[add] or drop not in squads[m]:
                continue
            squads[m].remove(drop)
            squads[m].append(add)
            owned[add] = True
            owned[drop] = False
            moves.append((gw, m, add, drop, gain))
            wins += 1
    return moves


def run_gameweek(brains, sv, cfg, squads, gw):
    """Line-ups, automatic substitutions, realized points per manager."""
    pts = np.zeros(N_MANAGERS)
    bench_pts = np.zeros(N_MANAGERS)
    xis = []
    played = sv.played[:, gw - 1]
    for m in range(N_MANAGERS):
        rows = np.array(squads[m])
        base = sv.base_ep1[rows, gw - 1]
        ep = brains[m].score("lineup", sv.key, sv.Xn, rows, gw, base,
                             feats=sv.X[rows, gw - 1])
        squad = [dict(name=int(r), pos=POSITIONS[sv.pos[r]], ep=float(e))
                 for r, e in zip(rows, ep)]
        xi, bench, _ = pick_xi(squad)
        final = autosub(xi, bench, played)
        idx = [p["name"] for p in final]
        pts[m] = sv.real[idx, gw - 1].sum()
        xis.append(sorted(idx))
        out = [r for r in rows if r not in idx]
        bench_pts[m] = sv.real[out, gw - 1].sum()
    return pts, bench_pts, xis


def simulate(brains, sv, cfg, rng, order=None, trace=False):
    """A full season. brains[i] is the manager in seat i."""
    order = list(order if order is not None else rng.permutation(N_MANAGERS))
    squads, owned = run_draft(brains, sv, cfg, rng, order)
    totals = np.zeros(N_MANAGERS)
    log = (dict(moves=[], weekly=[], xi=[], rosters=[], squads=None)
           if trace else None)
    tie = rng.permutation(N_MANAGERS)
    for gw in range(1, 39):
        if gw >= cfg.waiver_first_gw:
            priority = sorted(range(N_MANAGERS),
                              key=lambda m: (totals[m], tie[m]))
            mv = run_waivers(brains, sv, cfg, squads, owned, gw,
                             list(totals), priority)
            if trace:
                log["moves"] += mv
        pts, bench, xis = run_gameweek(brains, sv, cfg, squads, gw)
        totals += pts
        if trace:
            log["weekly"].append((gw, pts.copy(), bench.copy()))
            log["xi"].append(xis)
            log["rosters"].append([sorted(s) for s in squads])
    if trace:
        log["squads"] = [list(s) for s in squads]
        log["order"] = order
        return totals, log
    return totals, None

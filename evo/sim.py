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
  Free
  agency    once waivers have processed, everyone still unowned is a free
            agent until the deadline, first come first served. Modelled
            as a random order each week, which is the honest stand-in for
            who happens to look at their phone first. Same head as the
            waiver, told by its context which window it is in, but on the
            LATER clock: a day more team news, and after every rival's
            waiver has already landed.
  Line-up   11 starters, exactly 1 GKP, 3-5 DEF, 2-5 MID, 1-3 FWD, no
            captain, chosen by schwaddy.lineup.pick_xi so that the rules
            here and the rules on the dashboard cannot drift apart. Also
            on the deadline clock, because that is when a team sheet is
            actually submitted.
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
        # the deadline clock: free agency and the team sheet
        self.key_dl = key + "@dl"
        self.Xn_dl = standardizer.transform(arrays["X_dl"])
        self.X_dl = arrays["X_dl"]
        self.base_ep1_dl = arrays["base_ep1_dl"]
        self.base_next5_dl = arrays["base_next5_dl"]
        self.pos = arrays["pos"].astype(int)
        self.n = self.X.shape[0]
        self.real = arrays["real"]
        self.played = arrays["minutes"] > 0
        self.pool = arrays["pool"]
        self.base_season = arrays["base_season"]
        self.base_next5 = arrays["base_next5"]
        self.base_ep1 = arrays["base_ep1"]


def _need_positions(need):
    """Positions a manager may still take.

    Forced fill needs no special case: the quota sums to fifteen and one
    unit of it is spent per pick, so the remaining needs always sum to
    the remaining picks. A position with need left is therefore always
    legal, and one without never is.
    """
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
        allowed = _need_positions(need[m])
        cand = np.flatnonzero(avail0 & ~owned)
        cand = cand[np.isin(sv.pos[cand], [POS_ID[p] for p in allowed])]
        if len(cand) == 0:
            # ninety picks out of six hundred registered players; if this
            # ever fires the pool is wrong, and quietly drafting an
            # illegal squad instead would be far worse
            raise RuntimeError(
                f"draft pick {pick_no}: no eligible player at {allowed} "
                f"in {sv.key}")
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
                gap, mine_mask, cpos, is_fa=0.0):
    k = len(cand)
    ctx = np.zeros((k, CONTEXT["waiver"]), np.float32)
    ctx[:, 6] = is_fa
    ctx[:, 0] = gws_left / 38.0
    ctx[:, 1] = squad_pos_strength[cpos] / 20.0
    ctx[:, 2] = n_at_pos[cpos] / 5.0
    ctx[:, 3] = rank / 5.0
    ctx[:, 4] = np.tanh(gap / 100.0)
    ctx[:, 5] = mine_mask
    return ctx


def _rank_swaps(brain, sv, cfg, squad, free_rows, gw, totals, m, is_fa):
    """Best (gain, add, drop) pairs for one manager in one window.

    Free agency reads the deadline clock and the waiver reads the one a
    day earlier, which is the whole reason the two are separate decisions
    rather than the same one asked twice.
    """
    Xn, Xr = (sv.Xn_dl, sv.X_dl) if is_fa else (sv.Xn, sv.X)
    key = sv.key_dl if is_fa else sv.key
    base_all = sv.base_next5_dl if is_fa else sv.base_next5
    sq = np.array(squad)
    spos = sv.pos[sq]
    strength = np.zeros(4)
    n_at = np.zeros(4)
    for p in range(4):
        strength[p] = base_all[sq[spos == p], gw - 1].sum()
        n_at[p] = (spos == p).sum()
    rank = float(sorted(totals, reverse=True).index(totals[m]))
    gap = totals[m] - max(totals)

    # shortlist on the shared heuristic board, then let the head reorder:
    # scoring six hundred free agents with every genome every week is the
    # whole cost of the simulation
    fb = base_all[free_rows, gw - 1]
    pick = free_rows
    if len(free_rows) > cfg.waiver_shortlist:
        top = np.argpartition(-fb, cfg.waiver_shortlist)[:cfg.waiver_shortlist]
        pick = free_rows[top]
    cand = np.concatenate([pick, sq])
    mine = np.concatenate([np.zeros(len(pick)), np.ones(len(sq))])
    cpos = sv.pos[cand]
    base = base_all[cand, gw - 1]
    ctx = _waiver_ctx(sv, cand, strength, n_at, 39 - gw, rank, gap, mine,
                      cpos, is_fa=1.0 if is_fa else 0.0)
    val = brain.score("waiver", key, Xn, cand, gw, base, ctx=ctx,
                      feats=Xr[cand, gw - 1])
    unit = float(np.std(base)) or 1.0
    margin = max(0.0, brain.margin) * unit
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
    return pairs


def run_free_agency(brains, sv, cfg, squads, owned, gw, totals, rng):
    """The window between waivers processing and the deadline.

    No priority order: whoever gets there first takes the player, so the
    order is drawn fresh each week. A manager acts on what he can see
    AFTER everyone's waivers have landed, which is why this runs on the
    post-waiver ownership rather than a snapshot of it.
    """
    moves = []
    order = list(rng.permutation(N_MANAGERS))
    for m in order:
        for _ in range(cfg.fa_max_moves):
            free_rows = np.flatnonzero(sv.pool[:, gw - 1] & ~owned)
            if len(free_rows) == 0:
                break
            pairs = _rank_swaps(brains[m], sv, cfg, squads[m], free_rows, gw,
                                list(totals), m, is_fa=True)
            if not pairs:
                break
            gain, add, drop = pairs[0]
            squads[m].remove(drop)
            squads[m].append(add)
            owned[add] = True
            owned[drop] = False
            moves.append((gw, m, add, drop, gain, "f"))
    return moves


def run_waivers(brains, sv, cfg, squads, owned, gw, totals, priority):
    """One waiver window. Mutates squads/owned; returns the moves made."""
    n = sv.n
    free = sv.pool[:, gw - 1] & ~owned
    free_rows = np.flatnonzero(free)
    if len(free_rows) == 0:
        return []
    claims = {m: _rank_swaps(brains[m], sv, cfg, squads[m], free_rows, gw,
                             list(totals), m, is_fa=False)[:cfg.max_claims]
               for m in range(N_MANAGERS)}

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
            moves.append((gw, m, add, drop, gain, "w"))
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
        base = sv.base_ep1_dl[rows, gw - 1]
        ep = brains[m].score("lineup", sv.key_dl, sv.Xn_dl, rows, gw, base,
                             feats=sv.X_dl[rows, gw - 1])
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
            if cfg.free_agency:
                mv += run_free_agency(brains, sv, cfg, squads, owned, gw,
                                      list(totals), rng)
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

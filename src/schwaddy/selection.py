"""Which predictor's WAIVER CLAIMS actually held up, over five gameweeks.

backtest.py scores the XI, which is the wrong question for a waiver: an
XI is eleven nailed starters everybody agrees on, so every method picks
nearly the same team and the metric barely moves. A claim is different -
it is a choice among fringe players over a horizon, and that is where the
methods separate.

Protocol at every origin gameweek, with nothing after it visible:
  - "rostered" is the ROSTER players with the most minutes over the
    trailing eight gameweeks. Deliberately a minutes rule rather than a
    points one, so the free-agent pool is not defined by any predictor
    being compared and none has its own best picks removed from it.
  - each predictor claims the best free agent at EACH position on its own
    five-gameweek expected points. One per position, so a method that
    piles into whichever position it happens to like cannot win on
    concentration - and a claim is a position decision anyway.
  - the claim is scored on what those players ACTUALLY returned over the
    five gameweeks, zero for a week they did not play.

trop, b1 and b2 share the same trailing P(play), as production applies.
FPL's xP carries its own availability view; it is a one-gameweek number,
so the only five-week reading available from it is its per-match rate
times the fixtures scheduled - which is itself the finding about using it
for this decision.

Measured over 23/24, 24/25 and 25/26, 33 origins each, points returned by
the four claims:

                  23/24   24/25   25/26     vs trop, pooled
  trop             81.8    88.2    88.2                   -
  fpl x fixtures   84.3    77.6   (49.1)      -4.1  t=-1.3
  b1 shrink        68.9    72.1    74.4     -14.3  t=-8.3
  b2 last-4 form   68.5    58.4    65.3     -22.0  t=-10.0
  median free agent 19.1   16.1    21.0
  hindsight ceiling 135.6 136.6   137.6

25/26's FPL figure is bracketed and excluded: the archive carries xP for
only 28% of that season's appearances against ~90% in the other two.

Form-chasing is the WORST way to make a claim, in every season, by a
wide margin - though it was marginally ahead on single-gameweek XI picks,
which is the trap. And the model's edge is not bigger hauls, it is not
picking players who then do not play: it busts (under 5 points across all
five gameweeks) on 0-3% of claims against 10-18% for the two baselines.

Run as: python -m schwaddy.selection <season_idx> [data_dir]
"""
import csv
import json
import sys

import numpy as np

from .panel import build, SEASONS
from .mc import TropForecast
from .backtest import benchmarks, trailing_pplay, make_sched_M

HORIZON = 5
ROSTER = 90               # a six-manager draft league rosters ninety
POSITIONS = ("GKP", "DEF", "MID", "FWD")
BUST = 5.0                # points across all five gameweeks


def fpl_xp(data_dir, season, meta):
    """FPL's own published expected points, per player-gameweek."""
    code_of = {r["id"]: r["code"] for r in csv.DictReader(
        open(f"{data_dir}/players_raw_{SEASONS[season]}.csv"))}
    xp = np.zeros((len(meta["codes"]), 38))
    for r in csv.DictReader(open(f"{data_dir}/gws_{SEASONS[season]}.csv")):
        i = meta["row_of"].get(code_of.get(r["element"]))
        g = int(float(r["GW"])) - 1
        if i is not None and 0 <= g < 38:
            xp[i, g] += float(r.get("xP") or 0)
    return xp


def run(data_dir=".", season=4, lambda_time=0.03, verbose=True):
    Y, D, X, season_of, gw_of, meta = build(data_dir)
    base = season * 38
    M, MINS = meta["M"], meta["MINS"]
    real = Y * M
    N = Y.shape[0]
    pos_idx = {i: meta["pos_of"].get(c, "MID")
               for i, c in enumerate(meta["codes"])}
    POSN = {p: k for k, p in enumerate(POSITIONS)}
    groups = np.array([POSN[pos_idx[i]] for i in range(N)])
    sched_M = make_sched_M(data_dir, SEASONS, meta["codes"])

    xp = fpl_xp(data_dir, season, meta)
    # only trust FPL's column where the archive actually populated it
    have_fpl = (xp > 0).sum() > 0.5 * (D[:, base:base + 38] > 0).sum()

    m = TropForecast(lambda_time=lambda_time, lambda_nn=np.inf,
                     ridge_unit=15.0, unit_groups=groups, max_iter=300)
    rows = []
    for g in range(1, 39 - HORIZON):
        col = base + g - 1
        Dtr = D.copy()
        Dtr[:, col:] = 0                   # terminal mask, true forecasting
        m.fit(Y, Dtr, season_of, gw_of, X=X)
        b1, b2 = benchmarks(Y, D, pos_idx, col, base=base)
        pp = np.array([trailing_pplay(D, i, col, base=base) for i in range(N)])
        cols = list(range(col, col + HORIZON))
        sm = np.array([[sched_M(i, c) for c in cols] for i in range(N)])
        tot_m = sm.sum(1)

        trailing = MINS[:, max(0, col - 8):col].sum(1)
        rostered = set(np.argsort(-trailing)[:ROSTER])
        free = np.array([i for i in range(N)
                         if i not in rostered and trailing[i] > 0])
        if len(free) < 40:
            continue

        got = real[:, col:col + HORIZON].sum(1)
        preds = {"trop": (m.pred_[:, col:col + HORIZON] * sm).sum(1) * pp,
                 "b1": b1 * pp * tot_m,
                 "b2": b2 * pp * tot_m}
        if have_fpl:
            preds["fpl"] = xp[:, g - 1] / np.maximum(sm[:, 0], 1.0) * tot_m

        r = dict(gw=g, n_free=len(free))
        for k, v in preds.items():
            tot = 0.0
            for pos in POSITIONS:
                fp = np.array([i for i in free if pos_idx[i] == pos])
                if not len(fp):
                    continue
                top = fp[int(np.argmax(v[fp]))]
                tot += float(got[top])
                r[f"pts_{k}_{pos}"] = float(got[top])
                r[f"pick_{k}_{pos}"] = meta["codes"][top]
            r["pts_" + k] = tot
        r["pts_best"] = float(sum(
            got[np.array([i for i in free if pos_idx[i] == pos])].max()
            for pos in POSITIONS
            if any(pos_idx[i] == pos for i in free)))
        rows.append(r)
        if verbose:
            print(f"GW{g:>2}  " + "  ".join(
                f"{k} {r['pts_' + k]:5.1f}" for k in preds))
    return rows, list(preds)


def summarize(rows, keys):
    out = {}
    for k in keys + ["best"]:
        out[k] = dict(mean=float(np.mean([r["pts_" + k] for r in rows])))
    for k in keys:
        for pos in POSITIONS:
            v = [r[f"pts_{k}_{pos}"] for r in rows if f"pts_{k}_{pos}" in r]
            if v:
                out[k][pos] = float(np.mean(v))
                out[k]["bust_" + pos] = float(np.mean([x < BUST for x in v]))
        d = np.array([r["pts_" + k] for r in rows]) \
            - np.array([r["pts_trop"] for r in rows])
        out[k]["vs_trop"] = float(d.mean())
        out[k]["se"] = float(d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 1 else 0.0
    return out


if __name__ == "__main__":
    season = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    data_dir = sys.argv[2] if len(sys.argv) > 2 else "."
    rows, keys = run(data_dir, season)
    s = summarize(rows, keys)
    print(f"\n=== {SEASONS[season]}, {len(rows)} origin gameweeks ===")
    print(f"{'claim by':<10}{'total':>8}" +
          "".join(p.rjust(7) for p in POSITIONS) + f"{'vs trop':>10}{'t':>7}")
    for k in keys:
        t = (s[k]["vs_trop"] / s[k]["se"]) if s[k]["se"] else 0.0
        print(f"{k:<10}{s[k]['mean']:>8.1f}" +
              "".join(f"{s[k].get(p, 0):>7.1f}" for p in POSITIONS) +
              f"{s[k]['vs_trop']:>+10.1f}{t:>7.2f}")
    print(f"{'hindsight':<10}{s['best']['mean']:>8.1f}")
    print("\nbust rate (under 5 points across all five gameweeks)")
    for k in keys:
        print(f"  {k:<8}" + "".join(
            f"{s[k].get('bust_' + p, 0):>7.0%}" for p in POSITIONS))

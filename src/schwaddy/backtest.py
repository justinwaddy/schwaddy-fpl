"""Rolling-origin backtest of TROP-forecast on the 2025/26 season.

Defaults track refresh.py: ridge_unit=15.0 and production's p_plays. A
harness whose defaults differ from what ships validates a model nobody
runs - measured over 25/26, ridge_unit=0.0 costs 3.0 realized XI points a
gameweek against the 15.0 that production actually uses.

Protocol, no leakage:
1. Lambdas are selected by placebo CV using ONLY data through 2024/25
   (all of 25/26 masked), then held fixed for the whole backtest.
2. For each origin gameweek g = 1..38 of 25/26: fit on 21/22-24/25 plus
   25/26 gameweeks before g, forecast the GW g column. The opponent
   covariate entering GW g uses only matches before g by construction.
3. Metrics on players who actually played in GW g:
   - RMSE of predicted vs realized draft points
   - Spearman rank correlation
4. Decision test: each GW pick a legal XI (formation rules) by
   ep = prediction * trailing P(play), score the XI's REALIZED points
   (non-starters contribute 0, the realistic cost of a bad pick).
   Compared against the same procedure using two benchmarks and the
   hindsight-best XI ceiling.

Benchmarks:
  B1 shrink: season-to-date mean, shrunk (k=10) toward last season's mean
     (position mean if no history)
  B2 form: mean of the player's last 4 played matches (any season)
"""
import csv, sys
import numpy as np
from scipy.stats import spearmanr

from .panel import build
from .mc import TropForecast
from .lineup import pick_xi, p_plays

SEASON = 4                # default target: 25/26 (index 4)
BASE = SEASON * 38


def trailing_pplay(D, i, col, window=8, base=BASE):
    """Availability for the decision test, via production's p_plays.

    This used its own 0.2 + 0.8 * rate, which meant the harness scored an
    XI that refresh.py would never have picked. Anything measured here has
    to be measured on what ships.
    """
    lo = max(0, col - window)
    if col == base:                       # season opener: use last season
        lo, col = base - 38, base
    return p_plays(D[i, lo:col], "a", None)


def benchmarks(Y, D, pos_idx, col, base=BASE):
    """Return (B1 shrink, B2 form) predictions for column col."""
    N = Y.shape[0]
    season_cols = slice(base, col)
    prior_cols = slice(base - 38, base)
    b1 = np.zeros(N)
    b2 = np.zeros(N)
    pos_mean = {}
    prior_rate = np.zeros(N)
    for i in range(N):
        dp = D[i, prior_cols] > 0
        prior_rate[i] = Y[i, prior_cols][dp].mean() if dp.any() else np.nan
    for p in set(pos_idx.values()):
        sel = np.array([j for j in range(N) if pos_idx[j] == p], dtype=int)
        if len(sel) == 0:
            pos_mean[p] = 2.0
            continue
        v = prior_rate[sel]
        pos_mean[p] = np.nanmean(v) if np.isfinite(v).any() else 2.0
    for i in range(N):
        ds = D[i, season_cols] > 0
        n = int(ds.sum())
        std = Y[i, season_cols][ds].mean() if n else 0.0
        pri = prior_rate[i] if np.isfinite(prior_rate[i]) \
            else pos_mean[pos_idx[i]]
        b1[i] = (n * std + 10.0 * pri) / (n + 10.0)
        dall = np.where(D[i, :col] > 0)[0]
        b2[i] = Y[i, dall[-4:]].mean() if len(dall) else pri
    return b1, b2


def team_sched(data_dir, seasons):
    """(season_idx, team, gw) -> scheduled match count; and
    (season_idx, code, gw) -> team, from the gameweek files."""
    sched, code_team = {}, {}
    for si, s in enumerate(seasons):
        code_of = {r["id"]: r["code"] for r in
                   csv.DictReader(open(f"{data_dir}/players_raw_{s}.csv"))}
        fixs = {}
        for r in csv.DictReader(open(f"{data_dir}/gws_{s}.csv")):
            gw = int(float(r["GW"])) - 1
            if not (0 <= gw < 38):
                continue
            fixs.setdefault((r["team"], gw), set()).add(r["fixture"])
            c = code_of.get(r["element"])
            if c:
                code_team[(si, c, gw)] = r["team"]
        for (tm, gw), fset in fixs.items():
            sched[(si, tm, gw)] = len(fset)
    return sched, code_team


def make_sched_M(data_dir, seasons, codes):
    sched, code_team = team_sched(data_dir, seasons)
    def f(i, col):
        si, gw = col // 38, col % 38
        c = codes[i]
        for g in range(gw, -1, -1):
            tm = code_team.get((si, c, g))
            if tm:
                return float(sched.get((si, tm, gw), 1))
        return 1.0
    return f


def run(data_dir=".", origins=range(1, 39), max_iter=300, verbose=True,
        cv_kind="utility", season_idx=SEASON, ridge_unit=15.0,
        fixture_mode="basic", collect=False):
    """collect: also return the raw prediction column each gameweek, so a
    caller can score the WITHIN-player fixture signal (does a player's
    forecast rise and fall across his own gameweeks the way his realized
    points do?). The pooled metrics above cannot see that: they are
    dominated by between-player differences, which a fixture term does
    not touch."""
    Y, D, X, season_of, gw_of, meta = build(data_dir, fixture_mode=fixture_mode)
    base = season_idx * 38
    M = meta["M"]
    real = Y * M                               # realized GW totals
    from .panel import SEASONS
    sched_M = make_sched_M(data_dir, SEASONS, meta["codes"])
    pos_idx = {i: meta["pos_of"].get(c, "MID")
               for i, c in enumerate(meta["codes"])}

    # (1) lambda selection on pre-25/26 data only, under the chosen loss
    Dpre = D.copy()
    Dpre[:, base:] = 0
    POSN = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
    groups = np.array([POSN[pos_idx[i]] for i in range(Y.shape[0])])
    m = TropForecast(max_iter=max_iter, ridge_unit=ridge_unit,
                     unit_groups=groups)
    if cv_kind == "utility":
        best = m.cv_utility(Y, Dpre, season_of, gw_of, pos_idx, X=X,
                            time_grid=(0.0, 0.01, 0.03, 0.06),
                            nn_grid=(5.0, 10.0, np.inf),
                            cv_holdout=6, n_blocks=3, verbose=False)
        if verbose:
            print(f"pre-25/26 utility CV: XI/gw={best[0]:.2f} "
                  f"lambda_time={best[1]} lambda_nn={best[2]}")
    else:
        best = m.cv(Y, Dpre, season_of, gw_of, X=X,
                    time_grid=(0.0, 0.01, 0.03, 0.06),
                    nn_grid=(5.0, 10.0, np.inf),
                    cv_holdout=6, n_blocks=2, verbose=False)
        if verbose:
            print(f"pre-25/26 CV: rmse={best[0]:.4f} "
                  f"lambda_time={best[1]} lambda_nn={best[2]}")

    rows = []
    for g in origins:
        col = base + g - 1
        Dtr = D.copy()
        Dtr[:, col:] = 0                   # terminal mask, true forecasting
        m.fit(Y, Dtr, season_of, gw_of, X=X)
        played = np.where(D[:, col] > 0)[0]
        if len(played) < 30:
            continue
        yhat = m.pred_[played, col]
        b1, b2 = benchmarks(Y, D, pos_idx, col, base=base)
        y = Y[played, col]                     # per-match accuracy metrics
        res = dict(gw=g,
                   rmse=float(np.sqrt(np.mean((yhat - y) ** 2))),
                   rmse_b1=float(np.sqrt(np.mean((b1[played] - y) ** 2))),
                   rmse_b2=float(np.sqrt(np.mean((b2[played] - y) ** 2))),
                   rho=float(spearmanr(yhat, y).statistic),
                   rho_b1=float(spearmanr(b1[played], y).statistic))
        for label, pred in (("xi", m.pred_[:, col]), ("xi_b1", b1),
                            ("xi_b2", b2)):
            sq = [dict(name=i, pos=pos_idx[i],
                       ep=pred[i] * trailing_pplay(D, i, col, base=base)
                          * sched_M(i, col))
                  for i in range(Y.shape[0])]
            xi, _, _ = pick_xi(sq)
            res[label] = float(sum(real[p["name"], col] for p in xi))
        sq = [dict(name=i, pos=pos_idx[i], ep=real[i, col] * (D[i, col] > 0))
              for i in range(Y.shape[0])]
        xi, _, _ = pick_xi(sq)
        res["xi_best"] = float(sum(real[p["name"], col] for p in xi))
        if collect:
            res["played"] = played
            res["pred"] = yhat.copy()
            res["y"] = y.copy()
        rows.append(res)
        if verbose:
            print(f"GW{g:>2}  rmse {res['rmse']:.3f} (b1 {res['rmse_b1']:.3f} "
                  f"b2 {res['rmse_b2']:.3f})  rho {res['rho']:.3f} "
                  f"(b1 {res['rho_b1']:.3f})  XI {res['xi']:.0f} "
                  f"(b1 {res['xi_b1']:.0f} b2 {res['xi_b2']:.0f} "
                  f"best {res['xi_best']:.0f})")
    return rows, best


if __name__ == "__main__":
    tgt = int(sys.argv[2]) if len(sys.argv) > 2 else SEASON
    rows, best = run(sys.argv[1] if len(sys.argv) > 1 else ".",
                     season_idx=tgt)
    k = len(rows)
    agg = {f: float(np.mean([r[f] for r in rows]))
           for f in ("rmse", "rmse_b1", "rmse_b2", "rho", "rho_b1",
                     "xi", "xi_b1", "xi_b2", "xi_best")}
    from .panel import SEASONS as _S
    print(f"\n=== {_S[tgt]} backtest, {k} gameweeks ===")
    print(f"RMSE   trop {agg['rmse']:.3f} | shrink {agg['rmse_b1']:.3f} "
          f"| form {agg['rmse_b2']:.3f}")
    print(f"rho    trop {agg['rho']:.3f} | shrink {agg['rho_b1']:.3f}")
    print(f"XI/gw  trop {agg['xi']:.1f} | shrink {agg['xi_b1']:.1f} "
          f"| form {agg['xi_b2']:.1f} | hindsight {agg['xi_best']:.1f}")
    print(f"season XI total: trop {agg['xi']*k:.0f} | shrink "
          f"{agg['xi_b1']*k:.0f} | form {agg['xi_b2']*k:.0f}")

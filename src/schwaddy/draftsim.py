"""Simulate a 6-manager FPL Draft league over 2025/26.

Six manager strategies, each with its own draft board, weekly XI rule,
and waiver rule, all using only information available at the time:

  TROP    model season projection for the draft; refit one-GW-ahead
          prediction for XI; refit rest-of-season projection for waivers
  SHRINK  shrunk season-to-date per-match mean (last-season prior)
  FORM    mean of last 4 played matches
  LASTPTS last season's total points, never updated within season
  PPG     last season's points per game, season-to-date PPG in season
  STATIC  drafts by last-season totals, XI by last-season rate, no waivers

Common mechanics for everyone: snake draft with 2/5/5/3 quota and
forced-fill; shared trailing P(play); XI by pick_xi on ep = value *
P(play); FPL auto-subs (a starter with no minutes is replaced by the
first legal bench player who played); one waiver transaction per GW from
GW3, claims processed in reverse-standings priority, claim fires when the
free agent's rest-of-season value beats the manager's weakest
same-position player by the margin (in that manager's own units).

Seat order rotates over 6 replications so each strategy occupies each
draft slot once. Aggregate (classic) scoring: season total decides.
"""
import numpy as np

from .panel import build
from .mc import TropForecast
from .lineup import pick_xi
from .backtest import BASE, trailing_pplay, benchmarks, make_sched_M

SQUAD = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
MANAGERS = ["TROP", "SHRINK", "FORM", "LASTPTS", "PPG", "STATIC"]
WAIVER_MARGIN_RATE = 0.35     # per-remaining-GW units, scale-free-ish


def prior_rates(Y, D, pos_idx):
    """Last-season (24/25) per-match rate, apps, totals per player."""
    N = Y.shape[0]
    cols = slice(BASE - 38, BASE)
    rate = np.zeros(N); apps = np.zeros(N); tot = np.zeros(N)
    for i in range(N):
        dp = D[i, cols] > 0
        apps[i] = dp.sum()
        tot[i] = Y[i, cols][dp].sum()
        rate[i] = tot[i] / apps[i] if apps[i] else 0.0
    pos_mean = {}
    for p in set(pos_idx.values()):
        sel = [j for j in range(N) if pos_idx[j] == p and apps[j] >= 4]
        pos_mean[p] = np.mean([rate[j] for j in sel]) if sel else 2.0
    shrunk = np.array([(apps[i] * rate[i] + 10 * pos_mean[pos_idx[i]])
                       / (apps[i] + 10) for i in range(N)])
    return rate, apps, tot, shrunk, pos_mean


def draft_boards(Y, D, pos_idx, trop_season_proj, in_game):
    """Season-value board per strategy (higher = drafted earlier)."""
    rate, apps, tot, shrunk, _ = prior_rates(Y, D, pos_idx)
    exp_apps = 38 * (0.35 + 0.65 * np.minimum(1.0, apps / 38.0))
    boards = {
        "TROP": trop_season_proj,
        "SHRINK": shrunk * exp_apps,
        "FORM": shrunk * exp_apps,          # no in-season form pre-draft
        "LASTPTS": tot,
        "PPG": rate * exp_apps,
        "STATIC": tot,
    }
    for k in boards:
        boards[k] = np.where(in_game, boards[k], -1e9)
    return boards


def snake_draft(boards, order, pos_idx, N):
    """Greedy VORP-ish draft: each pick takes best available on own board
    respecting quota, with forced fill when remaining picks = remaining
    positional needs. Returns manager -> list of player rows."""
    squads = {m: [] for m in order}
    need = {m: dict(SQUAD) for m in order}
    taken = np.zeros(N, bool)
    seq = []
    for rnd in range(15):
        seq += order if rnd % 2 == 0 else order[::-1]
    for m in seq:
        forced = {p for p in SQUAD
                  if need[m][p] == 15 - len(squads[m])
                  and sum(need[m].values()) == 15 - len(squads[m])}
        b = boards[m].copy()
        for i in range(N):
            p = pos_idx[i]
            if taken[i] or need[m][p] == 0 or (forced and p not in forced):
                b[i] = -1e18
        pick = int(np.argmax(b))
        squads[m].append(pick)
        taken[pick] = True
        need[m][pos_idx[pick]] -= 1
    return squads, taken


def autosub(xi, bench, played):
    """Replace non-playing starters with first legal playing bench player."""
    xi = list(xi); bench = list(bench)
    counts = {k: sum(p["pos"] == k for p in xi) for k in SQUAD}
    mins = {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}
    for j, st in enumerate(xi):
        if played[st["name"]]:
            continue
        for b in bench:
            if not played[b["name"]]:
                continue
            same = b["pos"] == st["pos"]
            legal = same or (st["pos"] != "GKP" and b["pos"] != "GKP"
                             and counts[st["pos"]] - 1 >= mins[st["pos"]]
                             and counts[b["pos"]] + 1 <= {"DEF": 5, "MID": 5,
                                                          "FWD": 3}[b["pos"]])
            if legal:
                counts[st["pos"]] -= 1
                counts[b["pos"]] += 1
                xi[j] = b
                bench.remove(b)
                break
    return xi


def run(data_dir=".", max_iter=300, verbose=True, n_perm=6, seed=11):
    Y, D, X, season_of, gw_of, meta = build(data_dir)
    N = Y.shape[0]
    M = meta["M"]
    real = Y * M                       # realized GW totals (per-match Y x count)
    from .panel import SEASONS
    schedM = make_sched_M(data_dir, SEASONS, meta["codes"])
    pos_idx = {i: meta["pos_of"].get(c, "MID")
               for i, c in enumerate(meta["codes"])}
    # in-game 25/26 = appeared at all that season (proxy for the player pool)
    in_game = D[:, BASE:BASE + 38].any(axis=1)
    rate, apps, tot, shrunk, pos_mean = prior_rates(Y, D, pos_idx)

    # --- TROP fits: pre-season + one per origin GW (cached) ---
    # horizon-specific lambdas: weekly decisions use the utility-CV
    # selection (0.03); the season-ahead DRAFT board uses flatter
    # recency (0.01), since 38-GW-ahead forecasts overweight end-of-
    # season form under heavy discounting
    m = TropForecast(lambda_time=0.03, lambda_nn=np.inf, max_iter=max_iter)
    preds = {}
    for g in range(1, 39):
        col = BASE + g - 1
        Dtr = D.copy(); Dtr[:, col:] = 0
        m.fit(Y, Dtr, season_of, gw_of, X=X)
        preds[g] = m.pred_[:, BASE:BASE + 38].copy()
        if verbose and g % 10 == 0:
            print(f"  fitted origin GW{g}")
    md = TropForecast(lambda_time=0.01, lambda_nn=np.inf, max_iter=max_iter)
    Dtr = D.copy(); Dtr[:, BASE:] = 0
    md.fit(Y, Dtr, season_of, gw_of, X=X)
    pp0 = np.array([0.35 + 0.65 * min(1.0, apps[i] / 38.0) for i in range(N)])
    Mfut = M[:, BASE:BASE + 38]
    trop_season = (md.pred_[:, BASE:BASE + 38] * Mfut).sum(axis=1) * pp0

    def value(mgr, g):
        """Per-GW value vector for XI at origin g (before GW g plays)."""
        col = BASE + g - 1
        if mgr == "TROP":
            return preds[g][:, g - 1]
        if mgr in ("SHRINK", "FORM"):
            b1, b2 = benchmarks(Y, D, pos_idx, col)
            return b1 if mgr == "SHRINK" else b2
        if mgr == "LASTPTS":
            return tot / 38.0
        if mgr == "PPG":
            v = np.zeros(N)
            for i in range(N):
                ds = D[i, BASE:col] > 0
                v[i] = Y[i, BASE:col][ds].mean() if ds.any() \
                    else shrunk[i]
            return v
        if mgr == "STATIC":
            return rate
        raise ValueError(mgr)

    def rest_value(mgr, g):
        Mrem = M[:, BASE + g - 1:BASE + 38].sum(axis=1)
        if mgr == "TROP":
            return (preds[g][:, g - 1:] * M[:, BASE + g - 1:BASE + 38]).sum(axis=1)
        return value(mgr, g) * Mrem

    rng = np.random.default_rng(seed)
    results = {mn: [] for mn in MANAGERS}
    wins = {mn: 0 for mn in MANAGERS}
    for rot in range(n_perm):
        if n_perm <= 6:
            order = MANAGERS[rot:] + MANAGERS[:rot]
        else:
            order = [MANAGERS[j] for j in rng.permutation(6)]
        boards = draft_boards(Y, D, pos_idx, trop_season, in_game)
        squads, taken = snake_draft(boards, order, pos_idx, N)
        totals = {mn: 0.0 for mn in MANAGERS}
        for g in range(1, 39):
            col = BASE + g - 1
            played = D[:, col] > 0
            # waivers from GW3, reverse-standings priority, one move each
            if g >= 3:
                for mn in sorted(MANAGERS, key=lambda x: totals[x]):
                    if mn == "STATIC":
                        continue
                    rv = rest_value(mn, g)
                    margin = WAIVER_MARGIN_RATE * (39 - g)
                    bestgain, bestpair = 0.0, None
                    for pos in SQUAD:
                        mine = [i for i in squads[mn] if pos_idx[i] == pos]
                        weak = min(mine, key=lambda i: rv[i])
                        fas = [i for i in range(N)
                               if not taken[i] and in_game[i]
                               and pos_idx[i] == pos]
                        if not fas:
                            continue
                        fa = max(fas, key=lambda i: rv[i])
                        gain = rv[fa] - rv[weak]
                        if gain > margin + bestgain:
                            bestgain, bestpair = gain, (weak, fa)
                    if bestpair:
                        w, f = bestpair
                        squads[mn].remove(w); squads[mn].append(f)
                        taken[w] = False; taken[f] = True
            for mn in MANAGERS:
                v = value(mn, g)
                sq = [dict(name=i, pos=pos_idx[i],
                           ep=v[i] * trailing_pplay(D, i, col)
                              * schedM(i, col))
                      for i in squads[mn]]
                xi, bench, _ = pick_xi(sq)
                xi = autosub(xi, bench, played)
                totals[mn] += float(sum(real[p["name"], col] for p in xi))
        rank = sorted(MANAGERS, key=lambda x: -totals[x])
        wins[rank[0]] += 1
        for mn in MANAGERS:
            results[mn].append(totals[mn])
        if verbose:
            print(f"rotation {rot + 1} (slot1={order[0]}): "
                  + "  ".join(f"{mn} {totals[mn]:.0f}" for mn in rank))
    print(f"\n=== 6-team draft league, 2025/26, {n_perm} seat orders ===")
    for mn in sorted(MANAGERS, key=lambda x: -np.mean(results[x])):
        r = results[mn]
        print(f"{mn:8} mean {np.mean(r):7.1f}  sd {np.std(r):5.1f}  "
              f"titles {wins[mn]}/{len(results[mn])}")
    return results, wins


if __name__ == "__main__":
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else ".")

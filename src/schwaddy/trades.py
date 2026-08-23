"""Trade evaluator for draft leagues with all trades allowed.

Two value scales per player:
- model value: rest-of-season expected points, pred x scheduled fixture
  count x P(play), the number the system believes
- consensus value: season-to-date PPG (shrunk to last season) x remaining
  scheduled matches, the number a typical human believes

A good trade offer LOOKS roughly fair on consensus but favours you on
model value: you send players consensus overrates, you target players it
underrates. suggest_trades ranks rival-owned targets and own-squad
makeweights by exactly that wedge.
"""
import numpy as np


def model_values(pred, M, pplay, base, gw):
    """Rest-of-season model value from column base+gw-1 on."""
    cols = slice(base + gw - 1, base + 38)
    return (pred[:, cols] * M[:, cols]).sum(axis=1) * pplay


def consensus_values(Y, D, M, base, gw, pos_idx, k=10.0):
    """Shrunk PPG x remaining matches: the typical human's valuation."""
    N = Y.shape[0]
    season = slice(base, base + gw - 1)
    prior = slice(base - 38, base)
    rate = np.zeros(N)
    prior_rate = np.full(N, np.nan)
    for i in range(N):
        dp = D[i, prior] > 0
        if dp.any():
            prior_rate[i] = Y[i, prior][dp].mean()
    pos_mean = {}
    for p in set(pos_idx.values()):
        v = [prior_rate[i] for i in range(N)
             if pos_idx[i] == p and np.isfinite(prior_rate[i])]
        pos_mean[p] = np.mean(v) if v else 2.0
    for i in range(N):
        ds = D[i, season] > 0
        n = int(ds.sum())
        std = Y[i, season][ds].mean() if n else 0.0
        pri = prior_rate[i] if np.isfinite(prior_rate[i]) \
            else pos_mean[pos_idx[i]]
        rate[i] = (n * std + k * pri) / (n + k)
    rem = M[:, base + gw - 1:base + 38].sum(axis=1)
    return rate * rem


def evaluate_trade(give, get, mv):
    """give/get: lists of player row indices. Positive net = good for you."""
    net = float(sum(mv[i] for i in get) - sum(mv[i] for i in give))
    return net


def suggest_trades(my_squad, rival_squads, mv, cv, pos_idx, names,
                   fairness=8.0, min_edge=10.0, top=8):
    """One-for-one suggestions: |consensus difference| <= fairness (the
    rival plausibly accepts) and model edge >= min_edge (worth doing).
    rival_squads: dict manager -> list of row indices."""
    out = []
    for mgr, squad in rival_squads.items():
        for tgt in squad:
            for mine in my_squad:
                if pos_idx[tgt] != pos_idx[mine]:
                    continue
                cons_diff = cv[tgt] - cv[mine]   # what THEY think they lose
                edge = mv[tgt] - mv[mine]
                if abs(cons_diff) <= fairness and edge >= min_edge:
                    out.append(dict(
                        to=mgr, get=names[tgt], give=names[mine],
                        pos=pos_idx[tgt], model_edge=round(edge, 1),
                        consensus_diff=round(cons_diff, 1)))
    return sorted(out, key=lambda t: -t["model_edge"])[:top]

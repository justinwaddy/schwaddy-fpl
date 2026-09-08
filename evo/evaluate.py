"""Measuring a genome against managers it never trained against.

The headline number is a PAIRED difference. The same league is played
twice - identical season, identical opponents, identical seat, identical
seed - once with the genome in the seat and once with the reference
manager in it. Everything except the policy is held fixed, so the
difference is the policy's, and its standard error is small enough to
read after a few dozen leagues rather than a few thousand.

The reference is the "baseline" heuristic, which is exactly the residual
policy's starting point. "Beats the baseline" therefore means the network
found something the shrunk-mean-times-availability manager did not, which
is the only claim worth making.
"""
import numpy as np

from .config import N_MANAGERS
from .net import Brain, Heuristic
from .sim import simulate


def _opponents(cfg, rng, k):
    kinds = list(Heuristic.KINDS)
    rng.shuffle(kinds)
    return [Heuristic(kinds[i % len(kinds)], cfg, int(rng.integers(1 << 20)))
            for i in range(k)]


def paired_leagues(cfg, n_leagues, seasons, seed):
    """League specifications shared by challenger and reference."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n_leagues):
        season = seasons[i % len(seasons)]
        seat = int(rng.integers(N_MANAGERS))
        kinds = list(Heuristic.KINDS)
        rng.shuffle(kinds)
        opp = [kinds[j % len(kinds)] for j in range(N_MANAGERS - 1)]
        out.append((season, seat, opp, int(rng.integers(1 << 30))))
    return out


def _play_one(cfg, views, spec, policy):
    season, seat, opp, seed = spec
    brains = []
    it = iter(opp)
    for m in range(N_MANAGERS):
        brains.append(policy if m == seat
                      else Heuristic(next(it), cfg, seed + m))
    totals, _ = simulate(brains, views[season], cfg,
                         np.random.default_rng(seed))
    rank = int(np.sum(totals > totals[seat]))
    return float(totals[seat]), rank, float(totals[seat] - totals.mean())


def head_to_head(genome, cfg, views, specs, reference="baseline"):
    """Paired comparison of a genome against a fixed heuristic."""
    a = np.zeros((len(specs), 3))
    b = np.zeros((len(specs), 3))
    # one Brain for every league: its encoder output is cached per season,
    # and rebuilding it per league was costing more than the leagues did
    pol = Brain(genome, cfg)
    ref = Heuristic(reference, cfg, 0)
    for i, sp in enumerate(specs):
        a[i] = _play_one(cfg, views, sp, pol)
        b[i] = _play_one(cfg, views, sp, ref)
    d = a[:, 0] - b[:, 0]
    n = len(specs)
    return dict(
        n=n,
        points=float(a[:, 0].mean()), ref_points=float(b[:, 0].mean()),
        d_points=float(d.mean()),
        se_points=float(d.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan"),
        win=float((a[:, 1] == 0).mean()), ref_win=float((b[:, 1] == 0).mean()),
        rank=float(a[:, 1].mean() + 1), ref_rank=float(b[:, 1].mean() + 1),
        margin=float(a[:, 2].mean()), ref_margin=float(b[:, 2].mean()),
        # the selection score: rank is what wins a draft league, points
        # are what a manager feels, and the paired difference is what is
        # actually being measured
        score=float(a[:, 2].mean() - b[:, 2].mean()))


def mixed_league(genomes, cfg, views, seasons, n_leagues, seed):
    """Genomes and heuristics in the same league, as a sanity check that
    the paired result survives direct competition."""
    rng = np.random.default_rng(seed)
    tot = {}
    for i in range(n_leagues):
        season = seasons[i % len(seasons)]
        kinds = list(Heuristic.KINDS)
        rng.shuffle(kinds)
        names, brains = [], []
        for j, g in enumerate(genomes):
            names.append(f"net{j}")
            brains.append(Brain(g, cfg))
        for k in kinds[:N_MANAGERS - len(genomes)]:
            names.append(k)
            brains.append(Heuristic(k, cfg, int(rng.integers(1 << 20))))
        o = rng.permutation(N_MANAGERS)
        brains = [brains[j] for j in o]
        names = [names[j] for j in o]
        totals, _ = simulate(brains, views[season], cfg,
                             np.random.default_rng(int(rng.integers(1 << 30))))
        for m, nm in enumerate(names):
            r = tot.setdefault(nm, [])
            r.append((float(totals[m]), int(np.sum(totals > totals[m])) == 0))
    return {k: dict(points=float(np.mean([x[0] for x in v])),
                    win=float(np.mean([x[1] for x in v])), n=len(v))
            for k, v in tot.items()}

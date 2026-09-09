"""How a trained policy weighs its choices.

    python -m evo.run explain --model data/evo_model.npz [--season 2025-26]

The network is a residual on a heuristic baseline: score = baseline +
scale * unit * net(x, context). So the honest question is not "what does
it think a player is worth" - the baseline decides most of that - but
"where, and by how much, does it move AWAY from the baseline, and on
what evidence". Three probes answer it, all on real cells of one season:

  sensitivity   nudge one input by a standard deviation, everything else
                held, and read how the head's score moves, in points.
                Averaged over every player in the pool at every gameweek.
                Sign says which way; size says how much it cares.
  importance    scramble one input across players and measure how much
                the head's ranking of them changes. A feature the head
                leans on reorders the board when scrambled.
  the genes     the scalars evolution sets directly: how far each head
                strays from its baseline at all, the switching margin,
                and what makes it more or less selective week to week.

None of this is a causal story about football; it is a description of
the function the weights compute, on the data it is used on.
"""
import numpy as np

from .config import SEASONS
from .features import FEATURE_NAMES, Standardizer, load_seasons
from .net import Brain, CONTEXT, HEADS

DRAFT_CTX = ["round", "picks_to_next", "need_here", "need_total",
             "my_GKP", "my_DEF", "my_MID", "my_FWD", "scarcity", "filled",
             "club_stack"]
WAIVER_CTX = ["gws_left", "my_strength_here", "n_here", "my_rank",
              "gap_to_leader", "is_mine", "is_free_agency", "blank_share",
              "double_share", "club_stack", "drop_in_xi", "add_beats_xi",
              "bench_ep"]
SQUAD_CTX = ["gws_left", "rank", "gap", "blank_share", "double_share",
             "bench_ep"]


def _cells(arrays, std, clock):
    X = arrays["X_dl" if clock == "dl" else "X"]
    pool = arrays["pool"]
    Xn = std.transform(X)
    rows = np.argwhere(pool)
    return Xn[rows[:, 0], rows[:, 1]], rows


def _head_raw(brain, head, Xn, ctx):
    H = np.tanh(Xn @ brain.p["W1"] + brain.p["b1"])
    w = brain.p[f"w_{head}"]
    out = H @ w[:H.shape[1]] + brain.p[f"b_{head}"][0]
    if ctx is not None:
        out = out + ctx @ w[H.shape[1]:]
    return out


def explain(model_path, cfg, season=None, top=14):
    z = np.load(model_path, allow_pickle=False)
    brain = Brain(z["best"], cfg)
    std = Standardizer(z["mean"], z["sd"])
    season = season or SEASONS[-1]
    a = load_seasons(cfg, [season])[season]

    print(f"model {model_path}  generation {int(z['gen'])}  "
          f"probed on {season}\n")

    # ------------------------------------------------------------ genes
    print("THE GENES - what evolution set directly")
    sc = brain.p["scale"]
    for k, h in enumerate(HEADS):
        print(f"  {h:7} scale {sc[k]:+.3f}   "
              f"(0 = plays the baseline exactly; the sign is arbitrary, "
              f"the size is how far it strays, in units of the board's spread)")
    print(f"  margin gene {brain.margin:+.3f} units of spread "
          f"(started at {cfg.margin0})")
    wm = brain.p["w_margin"]
    print("  margin scaling exp(w . situation): + means MORE selective when")
    for n, w in sorted(zip(SQUAD_CTX, wm), key=lambda t: -abs(t[1])):
        print(f"    {n:13} {w:+.3f}")
    print()

    # ------------------------------------------------ per-head probes
    units = dict(
        draft=float(np.std(a["base_season"][a["pool"][:, 0]])),
        waiver=float(np.std(a["base_next5"][a["pool"]])),
        lineup=float(np.std(a["base_ep1_dl"][a["pool"]])))
    for head, clock in (("lineup", "dl"), ("waiver", "wv"), ("draft", "wv")):
        Xn, rows = _cells(a, std, clock)
        if head == "draft":
            Xn = Xn[rows[:, 1] == 0]
        k = HEADS.index(head)
        pts = sc[k] * units[head]          # raw head units -> points
        nctx = CONTEXT[head]
        ctx = np.zeros((len(Xn), nctx), np.float32) if nctx else None
        base = _head_raw(brain, head, Xn, ctx)
        names = list(FEATURE_NAMES)
        # a column that never varies on these cells - ownership change at
        # the draft, the interaction columns when they are off - carries a
        # weight that was never trained on anything; its "sensitivity" is
        # noise and is not reported
        live_col = Xn.std(0) > 1e-6
        sens = []
        for i in range(Xn.shape[1]):
            if not live_col[i]:
                continue
            Xp = Xn.copy(); Xp[:, i] += 1.0
            d = (_head_raw(brain, head, Xp, ctx) - base) * pts
            sens.append((names[i], float(d.mean()), float(np.abs(d).mean())))
        if nctx:
            cn = DRAFT_CTX if head == "draft" else WAIVER_CTX
            for i in range(nctx):
                cp = ctx.copy(); cp[:, i] += 0.25
                d = (_head_raw(brain, head, Xn, cp) - base) * pts
                sens.append((f"[ctx] {cn[i]}", float(d.mean()),
                             float(np.abs(d).mean())))
        rng = np.random.default_rng(0)
        imp = []
        for i in range(Xn.shape[1]):
            if not live_col[i]:
                continue
            Xp = Xn.copy(); Xp[:, i] = rng.permutation(Xp[:, i])
            r = _head_raw(brain, head, Xp, ctx)
            rho = np.corrcoef(base, r)[0, 1] if base.std() > 0 else 1.0
            imp.append((names[i], 1.0 - float(rho)))
        horizon = {"lineup": "this gameweek", "waiver": "next five",
                   "draft": "the season"}[head]
        print(f"{head.upper()} HEAD - residual on the baseline, in points over "
              f"{horizon}; its typical size is "
              f"{float(np.abs(base * pts).mean()):.2f} against a baseline "
              f"spread of {units[head]:.2f}")
        print(f"  {'+1 sd of ...':26} {'moves score':>12} {'|move|':>8}")
        for n, m, am in sorted(sens, key=lambda t: -t[2])[:top]:
            print(f"  {n:26} {m:+12.3f} {am:8.3f}")
        print("  ranking leans on (1 - rank correlation when scrambled):")
        print("   " + ", ".join(f"{n} {v:.2f}"
                                for n, v in sorted(imp, key=lambda t: -t[1])[:8]))
        print()
    return 0

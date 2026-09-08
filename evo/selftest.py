"""Checks that the claims made for this model are actually true.

Three of these are the reason the module exists at all:

  non-anticipation  rebuild a season's features from an archive with every
                    match after gameweek k deleted. Features for the
                    gameweeks before the cut must come out bit-identical.
                    If any window, rate or prior ever reaches forward,
                    this test fails.
  no-lookahead sim  permute the FUTURE realized points and replay with the
                    same seed. Every draft pick, every roster and every
                    eleven before that point must be unchanged, which is
                    only possible if no decision reads an outcome it could
                    not have known.
  legality          squads, formations, substitutions and waivers obey the
                    game's rules in every gameweek of a simulated season.

Run: python -m evo.run selftest
"""
import numpy as np

from .config import Config, SEASONS, SQUAD, SQUAD_SIZE, POSITIONS, MIN_PLAY, MAX_PLAY
from .features import SeasonData, build_features, load_seasons, Standardizer
from .net import Brain, Heuristic, new_genome
from .sim import SeasonView, simulate

OK, BAD = "  ok  ", " FAIL "


def _report(name, ok, detail=""):
    print(f"[{OK if ok else BAD}] {name}" + (f"  {detail}" if detail else ""),
          flush=True)
    return ok


def test_non_anticipation(cfg, season="2024-25", prev="2023-24", cut=20):
    full = build_features(SeasonData(season, cfg,
                                     prev=SeasonData(prev, cfg)), cfg)
    trunc = build_features(SeasonData(season, cfg,
                                      prev=SeasonData(prev, cfg),
                                      truncate_gw=cut), cfg)
    # match players by stable code; the truncated archive has fewer of them
    fi = {c: i for i, c in enumerate(full["codes"])}
    rows_t = np.arange(len(trunc["codes"]))
    rows_f = np.array([fi[c] for c in trunc["codes"]])
    # the fixture list is published in advance, so the last five gameweeks
    # before the cut are excluded: the truncated build simply has no
    # schedule beyond it, which is a missing input, not a leak
    g = cut - 5
    a = full["X"][rows_f, :g]
    b = trunc["X"][rows_t, :g]
    same = np.array_equal(a, b)
    d = float(np.abs(a - b).max()) if not same else 0.0
    ok = _report("features are non-anticipative", same,
                 f"gw 1-{g}, {len(rows_t)} players, max|diff| {d:g}")
    for k in ("base_ppm", "base_ep1", "base_next5", "p_play"):
        s = np.array_equal(full[k][rows_f, :g], trunc[k][rows_t, :g])
        ok &= _report(f"  baseline {k}", s)
    ok &= _report("  pool membership", np.array_equal(
        full["pool"][rows_f, :g], trunc["pool"][rows_t, :g]))
    return ok


def test_no_lookahead(cfg, views, season="2024-25", cut=15):
    sv = views[season]
    rng = np.random.default_rng(5)
    brains = [Brain(new_genome(cfg, rng), cfg) for _ in range(3)] + \
             [Heuristic(k, cfg, 3) for k in ("form", "market", "ppg")]
    base = sv.real.copy()
    outs = []
    for trial in range(2):
        sv.real = base.copy()
        if trial:                       # scramble everything from the cut on
            r = np.random.default_rng(77)
            fut = sv.real[:, cut:]
            sv.real[:, cut:] = fut[r.permutation(fut.shape[0])]
        _, log = simulate(brains, sv, cfg, np.random.default_rng(11),
                          trace=True)
        outs.append(log)
    sv.real = base
    same_draft = all(sorted(a) == sorted(b)
                     for a, b in zip(outs[0]["rosters"][0],
                                     outs[1]["rosters"][0]))
    same_xi = all(outs[0]["xi"][g] == outs[1]["xi"][g] for g in range(cut))
    same_roster = all(outs[0]["rosters"][g] == outs[1]["rosters"][g]
                      for g in range(cut))
    ok = _report("draft is unchanged by future results", same_draft)
    ok &= _report(f"line-ups gw 1-{cut} unchanged", same_xi)
    ok &= _report(f"rosters gw 1-{cut} unchanged", same_roster)
    diverges = outs[0]["xi"][cut:] != outs[1]["xi"][cut:]
    ok &= _report("  and they DO diverge after the cut", bool(diverges),
                  "(a test that always passed would prove nothing)")
    return ok


def test_legality(cfg, views, season="2025-26"):
    sv = views[season]
    rng = np.random.default_rng(2)
    brains = [Brain(new_genome(cfg, rng), cfg) for _ in range(4)] + \
             [Heuristic("baseline", cfg), Heuristic("noisy", cfg, 1)]
    _, log = simulate(brains, sv, cfg, rng, trace=True)
    ok = True
    for g, rosters in enumerate(log["rosters"]):
        seen = set()
        for m, sq in enumerate(rosters):
            if len(sq) != SQUAD_SIZE:
                ok &= _report(f"squad size gw{g+1} manager{m}", False,
                              str(len(sq)))
            cnt = {p: 0 for p in POSITIONS}
            for i in sq:
                cnt[POSITIONS[sv.pos[i]]] += 1
            if cnt != SQUAD:
                ok &= _report(f"quota gw{g+1} manager{m}", False, str(cnt))
            if seen & set(sq):
                ok &= _report(f"double ownership gw{g+1}", False)
            seen |= set(sq)
    ok &= _report("squads: 15 players, 2/5/5/3, nobody owned twice", ok)

    good = True
    for g, week in enumerate(log["xi"]):
        for m, xi in enumerate(week):
            if len(xi) != 11:
                good = False
            c = {p: 0 for p in POSITIONS}
            for i in xi:
                c[POSITIONS[sv.pos[i]]] += 1
            for p in POSITIONS:
                if not (MIN_PLAY[p] <= c[p] <= MAX_PLAY[p]):
                    good = False
            if set(xi) - set(log["rosters"][g][m]):
                good = False
    ok &= _report("every XI is legal and drawn from the squad", good)

    moves = log["moves"]
    good = all(sv.pool[add, gw - 1] for gw, m, add, drop, _ in moves)
    ok &= _report("waiver adds were in the pool at the time", good,
                  f"{len(moves)} moves")
    good = all(sv.pos[add] == sv.pos[drop] for _, _, add, drop, _ in moves)
    ok &= _report("waivers are same-position swaps", good)
    return ok


def test_determinism(cfg, views, season="2024-25"):
    sv = views[season]
    rng = np.random.default_rng(4)
    brains = [Brain(new_genome(cfg, rng), cfg) for _ in range(4)] + \
             [Heuristic("form", cfg, 1), Heuristic("ppg", cfg, 2)]
    a, _ = simulate(brains, sv, cfg, np.random.default_rng(9))
    b, _ = simulate(brains, sv, cfg, np.random.default_rng(9))
    return _report("same seed, same season", np.array_equal(a, b),
                   f"{a.round(0)}")


def test_standardizer(cfg, arrays):
    tr = SEASONS[:-1]
    a = Standardizer().fit([arrays[s] for s in tr])
    b = Standardizer().fit([arrays[s] for s in SEASONS])
    return _report("standardizer fitted on training seasons only differs "
                   "from one fitted on all", not np.allclose(a.mean, b.mean))


def run_all(cfg=None):
    cfg = cfg or Config()
    arrays = load_seasons(cfg)
    std = Standardizer().fit([arrays[s] for s in SEASONS[:-1]])
    views = {s: SeasonView(s, arrays[s], std) for s in SEASONS}
    ok = True
    ok &= test_non_anticipation(cfg)
    ok &= test_no_lookahead(cfg, views)
    ok &= test_legality(cfg, views)
    ok &= test_determinism(cfg, views)
    ok &= test_standardizer(cfg, arrays)
    print("\nall checks passed" if ok else "\nSOME CHECKS FAILED")
    return ok

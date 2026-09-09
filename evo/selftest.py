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
from .features import (SeasonData, build_season,
                       load_seasons, Standardizer)
from .net import Brain, Heuristic, new_genome
from .sim import SeasonView, simulate

OK, BAD = "  ok  ", " FAIL "


def _report(name, ok, detail=""):
    print(f"[{OK if ok else BAD}] {name}" + (f"  {detail}" if detail else ""),
          flush=True)
    return ok


def test_non_anticipation(cfg, season="2024-25", prev="2023-24", cut=20):
    fsd = SeasonData(season, cfg, prev=SeasonData(prev, cfg))
    tsd = SeasonData(season, cfg, prev=SeasonData(prev, cfg),
                     truncate_gw=cut)
    # the truncated archive has no RESULTS after the cut, and that is the
    # test. It must still have the SCHEDULE - who plays whom, where, and
    # when - because that was published in June; without it the rest-of-
    # season columns differ for the dull reason that the fixture list is
    # missing, not because anything read a result it should not have.
    tsd.fixtures = fsd.fixtures
    tsd.deadline = fsd.deadline.copy()
    tsd.t_dec = fsd.t_dec.copy()
    full = build_season(fsd, cfg)
    trunc = build_season(tsd, cfg)
    # match players by stable code; the truncated archive has fewer of them
    fi = {c: i for i, c in enumerate(full["codes"])}
    rows_t = np.arange(len(trunc["codes"]))
    rows_f = np.array([fi[c] for c in trunc["codes"]])
    # every gameweek before the cut, now that the schedule is supplied
    g = cut - 1
    a = full["X"][rows_f, :g]
    b = trunc["X"][rows_t, :g]
    same = np.array_equal(a, b)
    d = float(np.abs(a - b).max()) if not same else 0.0
    ok = _report("features are non-anticipative", same,
                 f"gw 1-{g}, {len(rows_t)} players, max|diff| {d:g}")
    # the deadline clock is a day later than the waiver clock, so it gets
    # the same treatment rather than being taken on trust
    a = full["X_dl"][rows_f, :g]
    b = trunc["X_dl"][rows_t, :g]
    ok &= _report("  and on the deadline clock too", np.array_equal(a, b))
    for k in ("base_ppm", "base_ep1", "base_next5", "p_play",
              "base_ep1_dl", "base_next5_dl", "p_play_dl"):
        s = np.array_equal(full[k][rows_f, :g], trunc[k][rows_t, :g])
        ok &= _report(f"  baseline {k}", s)
    ok &= _report("  pool membership", np.array_equal(
        full["pool"][rows_f, :g], trunc["pool"][rows_t, :g]))
    return ok


def test_return_dates(cfg):
    """The news line's return date is read, and a short absence is not
    priced as a long one."""
    from .injuries import expected_return
    from .features import FEATURE_NAMES
    import datetime as dt
    t0 = dt.datetime(2024, 8, 12, 14, 30, tzinfo=dt.timezone.utc).timestamp()
    r, k = expected_return("i", "Knee injury - Expected back 07 Oct", t0)
    ok = _report("'Expected back 07 Oct' parses",
                 k == 1.0 and abs(r - dt.datetime(2024, 10, 7, 12,
                 tzinfo=dt.timezone.utc).timestamp()) < 1, "")
    r, k = expected_return("i", "Hamstring injury - Expected back 15 Jan", t0)
    ok &= _report("  and a date past new year lands in the next year",
                  k == 1.0 and r > t0 + 100 * 86400)
    r, k = expected_return("i", "Knee injury - Unknown return date", t0)
    ok &= _report("  and no date means a long default, not a fit player",
                  k == 0.0 and r > t0 + 30 * 86400)
    r, k = expected_return("d", "Knock - 75% chance of playing", t0)
    ok &= _report("  and a knock is this round's question only",
                  k == 0.0 and r < t0 + 7 * 86400)
    # in the features: a doubtful player's five-week baseline recovers
    ix = {n: i for i, n in enumerate(FEATURE_NAMES)}
    d = load_seasons(cfg, ["2024-25"])["2024-25"]
    X, pool = d["X"], d["pool"]
    dbt = (X[:, :, ix["inj_doubt"]] > 0.5) & pool & (d["base_ep1"] > 0.5)
    ratio = d["base_next5"][dbt] / np.maximum(5 * d["base_ep1"][dbt], 1e-6)
    ok &= _report("a doubtful player's five-week value recovers after this "
                  "round", bool(dbt.sum()) and float(np.median(ratio)) > 1.05,
                  f"median next5 / (5 x this week) = {np.median(ratio):.2f} "
                  f"over {int(dbt.sum())} cells")
    # departed or on loan: out, no return date published, and the default
    # horizon sits at the cap - as opposed to an injured player with a
    # date, who is SUPPOSED to recover
    gone = ((X[:, :, ix["inj_out"]] > 0.5) & (X[:, :, ix["inj_factor"]] < 0.05)
            & (X[:, :, ix["inj_return_known"]] < 0.5)
            & (X[:, :, ix["inj_return_days"]] > 2.9) & pool)
    ok &= _report("  and a player who has left the club does not",
                  bool(gone.sum()) and bool(np.all(d["base_next5"][gone] < 0.5)),
                  f"{int(gone.sum())} cells")
    return ok


def test_fixture_horizon(cfg, season="2021-22"):
    """Beyond the horizon the schedule is one fixture a gameweek - the
    published shape - and inside it the archive's blanks and doubles are
    visible. 2021/22 is the season with the most of them."""
    from .features import FEATURE_NAMES, build_features
    from .config import Config
    ix = {n: i for i, n in enumerate(FEATURE_NAMES)}
    sd = SeasonData(season, cfg)
    c0 = Config(**{**cfg.to_dict(), "fixture_horizon": 0})
    d0 = build_features(sd, c0)
    pool = d0["pool"][:, :34]
    beyond = (d0["X"][:, :34, ix["n_fix5"]] * 5
              - d0["X"][:, :34, ix["n_fix1"]])[pool]
    ok = _report("beyond the horizon, one fixture a gameweek",
                 np.allclose(beyond, 4.0), "horizon 0: gameweeks +1..+4")
    d3 = build_features(sd, cfg)
    inside = (d3["X"][:, :34, ix["n_fix5"]] * 5
              - d3["X"][:, :34, ix["n_fix1"]])[pool]
    ok &= _report("and inside it the reschedulings are visible",
                  not np.allclose(inside, 4.0),
                  f"horizon {cfg.fixture_horizon}: "
                  f"{int((np.abs(inside - 4) > 1e-6).sum())} cells differ")
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
    kinds = {k: [m for m in moves if m[5] == k] for k in ("w", "f")}
    good = all(sv.pool[add, gw - 1] for gw, m, add, drop, _, _ in moves)
    ok &= _report("every add was in the pool at the time", good,
                  f"{len(kinds['w'])} waiver, {len(kinds['f'])} free agent")
    good = all(sv.pos[add] == sv.pos[drop] for _, _, add, drop, _, _ in moves)
    ok &= _report("every move is a same-position swap", good)
    # free agency runs after waivers, never before
    per_gw = {}
    for gw, m, add, drop, _, k in moves:
        per_gw.setdefault((gw, m), []).append(k)
    good = all("w" not in v[v.index("f"):] if "f" in v else True
               for v in per_gw.values())
    ok &= _report("free agency runs after the waiver, never before", good)
    good = all(sum(1 for x in v if x == "f") <= cfg.fa_max_moves
               for v in per_gw.values())
    ok &= _report("free-agent moves respect the per-week cap", good)
    # a player released this week is on waivers: no free-agent add may be
    # anyone dropped earlier the same gameweek, by anybody
    good = True
    by_gw = {}
    for gw, m, add, drop, _, k in moves:
        by_gw.setdefault(gw, []).append((m, add, drop, k))
    for gw, seq in by_gw.items():
        dropped = set()
        for m, add, drop, k in seq:
            if k == "f" and add in dropped:
                good = False
            dropped.add(drop)
    ok &= _report("no free-agent add is a player released that week", good)
    if cfg.max_success_per_gw:
        good = all(sum(1 for x in v if x == "w") <= cfg.max_success_per_gw
                   for v in per_gw.values())
        ok &= _report("waiver wins respect the cap", good)
    else:
        most = max((sum(1 for x in v if x == "w") for v in per_gw.values()),
                   default=0)
        ok &= _report("waivers are unlimited", True,
                      f"most wins by one manager in a week: {most}")
    # a manager never drops the same player twice in a week
    good = all(len([d for _, _, d, _ in
                    [(m, a, dr, k) for m, a, dr, k in seq if m == mm]])
               == len({d for _, _, d, _ in
                       [(m, a, dr, k) for m, a, dr, k in seq if m == mm]})
               for gw, seq in by_gw.items() for mm in range(6))
    ok &= _report("no player is dropped twice in a week", good)
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
    ok &= test_fixture_horizon(cfg)
    ok &= test_return_dates(cfg)
    ok &= test_no_lookahead(cfg, views)
    ok &= test_legality(cfg, views)
    ok &= test_determinism(cfg, views)
    ok &= test_standardizer(cfg, arrays)
    print("\nall checks passed" if ok else "\nSOME CHECKS FAILED")
    return ok

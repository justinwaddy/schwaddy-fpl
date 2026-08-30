"""Build the player-gameweek panel for TROP-forecast.

Rows: player code (stable across seasons). Columns: (season, gw), five
historical seasons plus the 38 future gameweeks of 2026/27.
Y: per-match points under 2026/27 DRAFT scoring recomputed from raw match
stats (DefCon fields exist only from 2025/26; earlier seasons score 0
there, partially absorbed by season effects).
D: 1 if the player played minutes that gameweek.
X: fixture covariates, all observed for future cells from the published
fixture list (double gameweeks use the first fixture, blanks are home=0
with D=0). Two modes:
  "basic" - home dummy plus the opponent's rolling goals conceded, one
            global slope each. This was the only mode until it was
            measured: it carries no opponent-attack term at all, so a
            keeper facing the division's best attack looked identical to
            one facing its worst except through that attack's leakiness,
            which is the wrong side of the ball for him.
  "both"  - adds the opponent's rolling goals SCORED, one pooled slope.
  "pos"   - adds it and interacts all three with the position group.

"basic" is the default because the other two were measured and REJECTED,
which is worth writing down so nobody re-derives the idea and re-ships it.
The motivation was sound: pooled over 21/22-25/26 the two opponent slopes
differ by position and by sign,

                       conceded   scored
              GKP        +0.37    -0.70
              DEF        +0.27    -1.09
              MID        +0.46    -0.37
              FWD        +0.56    -0.29

so for a defender the opponent's ATTACK is four times the term "basic"
was using, and "basic" does not carry it at all. Rolling-origin over
23/24, 24/25 and 25/26 the richer covariates do slightly improve
per-match accuracy - RMSE and the within-player rank correlation both
edge up - and they lose realized XI points in every season:

              XI/gw change vs basic     RMSE      within-player rho
  +attack     -3.68  -3.45  -4.45       ~equal    .076->.097 etc
  +attack*pos -3.47  -3.50  -6.13       ~equal    .076->.100 etc

Three seasons out of three, most t-stats past -2. The reading is that the
low-rank block already carries team-level attacking and defensive
strength, so an explicit opponent-attack term double-counts it, and the
extra swing it puts into the forecast reorders the top of the board
without being right often enough to pay for the churn. Accuracy on the
average player and accuracy on the eleven you actually pick are not the
same thing, and this panel is built for the second.
"""
import csv, json
import numpy as np

from .odds import load_historical, cell_covariates, BASELINE

SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
LIVE = "2026-27"

# Centre for the two opponent-strength covariates: the long-run league
# average goals per team per match. A fixed constant rather than a
# data-derived mean so that a cell built for a future gameweek is centred
# exactly as a historical one is.
GOALS_CENTRE = 1.4
# The home dummy is centred too, but only in the interacted mode. An
# interacted column with a non-zero mean inside its own position group
# can shift that whole group's level, and the unit effects cannot fully
# absorb it because they are ridge-shrunk toward the group mean. A shift
# in a group's level is exactly what changes the chosen formation.
HOME_CENTRE = 0.5
POS_GROUPS = ("GKP", "DEF", "MID", "FWD")
N_FIXTURE_X = {"basic": 2, "both": 3, "pos": 3 * len(POS_GROUPS)}


def fixture_x(mode, pos, home, oc, gs):
    """Fixture covariate vector for one player-gameweek cell.

    oc / gs: the opponent's rolling goals conceded / scored entering the
    gameweek. In "pos" mode each of the three enters only in the player's
    own position slot, so every group gets its own slope.
    """
    if mode == "basic":
        return [home, oc]
    if mode == "both":                 # opponent attack added, slopes pooled
        return [home, oc - GOALS_CENTRE, gs - GOALS_CENTRE]
    v = [0.0] * N_FIXTURE_X["pos"]
    k = POS_GROUPS.index(pos)
    v[k] = home - HOME_CENTRE
    v[4 + k] = oc - GOALS_CENTRE
    v[8 + k] = gs - GOALS_CENTRE
    return v


def _team_rates(data_dir, prior_mode="team", prior_w=16.0):
    """(conceded, scored), each (season_idx, team_name, gw) -> per-GW
    rolling mean goals entering that GW (season-to-date, shrunk toward a
    prior), plus a "final" key holding the finished-season mean per team.

    Both sides of the ball are built here because both matter and they
    matter to different positions: the opponent's conceded rate drives an
    attacker's returns, the opponent's scoring rate drives a defender's
    and a keeper's. Only the first existed until the second was measured.

    prior_mode "team" anchors each club to its own previous-season rate,
    falling back to a promoted-club prior for newcomers. "flat" is the old
    behaviour, a single league average for everyone, which discards what
    is already known about a club and leaves the covariate at the mercy of
    one match: measured on the live season, a single gameweek doubled the
    spread of the opponent term and left it correlated 0.27 with the
    settled rates it replaced.
    """
    import os
    conceded, scored = {}, {}
    live_list = [LIVE] if os.path.exists(f"{data_dir}/gws_{LIVE}.csv") else []
    prev_c, prev_s = {}, {}
    for si, s in enumerate(SEASONS + live_list):
        got_c, got_s = {}, {}
        for r in csv.DictReader(open(f"{data_dir}/gws_{s}.csv")):
            gw = int(float(r["GW"])) - 1
            if not (0 <= gw < 38):
                continue
            hs, as_ = r["team_h_score"], r["team_a_score"]
            if hs in ("", None) or as_ in ("", None):
                continue
            home = r["was_home"] in ("True", "TRUE", "1")
            gc = int(float(as_)) if home else int(float(hs))
            gs = int(float(hs)) if home else int(float(as_))
            got_c.setdefault((r["team"], gw), gc)
            got_s.setdefault((r["team"], gw), gs)
        teams = sorted({k[0] for k in got_c})
        # newcomers: a promoted side concedes like a poor side and scores
        # like one, so take opposite tails of last season's distribution
        prom_c = (float(np.quantile(list(prev_c.values()), 0.8))
                  if prev_c else GOALS_CENTRE)
        prom_s = (float(np.quantile(list(prev_s.values()), 0.2))
                  if prev_s else GOALS_CENTRE)
        for out, got, prev, prom in ((conceded, got_c, prev_c, prom_c),
                                     (scored, got_s, prev_s, prom_s)):
            for tm in teams:
                if prior_mode == "team":
                    prior = prev.get(tm, prom)
                else:
                    prior = GOALS_CENTRE
                run, n = 0.0, 0
                for gw in range(38):
                    out[(si, tm, gw)] = (run + prior_w * prior) / (n + prior_w)
                    if (tm, gw) in got:
                        run += got[(tm, gw)]
                        n += 1
                out[(si, tm, "final")] = run / max(n, 1)
        prev_c = {tm: conceded[(si, tm, "final")] for tm in teams}
        prev_s = {tm: scored[(si, tm, "final")] for tm in teams}
    return conceded, scored


def _team_conceded(data_dir, prior_mode="team", prior_w=16.0):
    """Conceded rates alone. Kept for callers that only want that half."""
    return _team_rates(data_dir, prior_mode, prior_w)[0]


def draft_points(r, pos, S, alpha=0.0):
    m = int(r["minutes"] or 0)
    if m == 0:
        return 0.0
    p = S["long_play"] if m >= S["long_play_limit"] else S["short_play"]
    g = int(r["goals_scored"] or 0)
    a = int(r["assists"] or 0)
    try:
        xg = float(r.get("expected_goals") or 0.0)
        xa = float(r.get("expected_assists") or 0.0)
    except ValueError:
        xg, xa = float(g), float(a)
    p += ((1 - alpha) * g + alpha * xg) * S[f"goals_scored_{pos}"]
    p += ((1 - alpha) * a + alpha * xa) * S["assists"]
    if m >= 60 and int(r["clean_sheets"] or 0):
        p += S[f"clean_sheets_{pos}"]
    if pos in ("GKP", "DEF"):
        p += (int(r["goals_conceded"] or 0) // S["concede_limit"]) \
             * S[f"goals_conceded_{pos}"]
    p += (int(r["saves"] or 0) // S["saves_limit"]) * S["saves"]
    dc = r.get("defensive_contribution")
    if dc not in (None, ""):
        lim = S[f"defensive_contribution_limit_{pos}"]
        if lim and float(dc) >= lim:
            p += S[f"defensive_contribution_{pos}"]
    p += int(r["bonus"] or 0) * S["bonus"]
    p += int(r["yellow_cards"] or 0) * S["yellow_cards"]
    p += int(r["red_cards"] or 0) * S["red_cards"]
    p += int(r["own_goals"] or 0) * S["own_goals"]
    p += int(r["penalties_saved"] or 0) * S["penalties_saved"]
    p += int(r["penalties_missed"] or 0) * S["penalties_missed"]
    return float(p)


def build(data_dir=".", min_career_apps=6, use_odds=False, alpha=0.0,
          conceded_prior="team", conceded_w=16.0, fixture_mode="basic"):
    d = json.load(open(f"{data_dir}/draft_bootstrap.json"))
    S = d["settings"]["scoring"]
    POS = {t["id"]: t["singular_name_short"] for t in d["element_types"]}

    n_hist = len(SEASONS)
    T = (n_hist + 1) * 38
    season_of = np.repeat(np.arange(n_hist + 1), 38)
    gw_of = np.tile(np.arange(38), n_hist + 1)

    conceded, scored = _team_rates(data_dir, prior_mode=conceded_prior,
                                   prior_w=conceded_w)
    odds = load_historical(data_dir, SEASONS) if use_odds else {}
    n_matched = [0, 0]
    cells = {}          # (code, col) -> (points, home, opp_conceded,
                        #   opp_scored, position, odds3, n_matches, minutes)
    apps = {}
    pos_of = {}
    import os
    live_ok = os.path.exists(f"{data_dir}/gws_{LIVE}.csv") \
        and os.path.exists(f"{data_dir}/players_raw_{LIVE}.csv") \
        and os.path.exists(f"{data_dir}/teams_{LIVE}.csv")
    for si, s in enumerate(SEASONS + ([LIVE] if live_ok else [])):
        code_of = {r["id"]: r["code"]
                   for r in csv.DictReader(open(f"{data_dir}/players_raw_{s}.csv"))}
        name_of_tid = {r["id"]: r["name"]
                       for r in csv.DictReader(open(f"{data_dir}/teams_{s}.csv"))}
        for r in csv.DictReader(open(f"{data_dir}/gws_{s}.csv")):
            code = code_of.get(r["element"])
            if not code:
                continue
            pos = "GKP" if r["position"] in ("GK", "GKP") else r["position"]
            if pos not in ("GKP", "DEF", "MID", "FWD"):
                continue
            gw = int(float(r["GW"])) - 1
            if not (0 <= gw < 38) or int(r["minutes"] or 0) == 0:
                continue
            col = si * 38 + gw
            pts = draft_points(r, pos, S, alpha=alpha)
            home = 1.0 if r["was_home"] in ("True", "TRUE", "1", True) else 0.0
            opp = name_of_tid.get(r["opponent_team"], "")
            oc = conceded.get((si, opp, gw), GOALS_CENTRE)
            gs = scored.get((si, opp, gw), GOALS_CENTRE)
            hm, aw = (r["team"], opp) if home else (opp, r["team"])
            mt = odds.get((si, hm, aw))
            n_matched[0] += mt is not None
            n_matched[1] += 1
            o3 = cell_covariates(mt, bool(home))
            key = (code, col)
            nm = 1
            mins = int(r["minutes"] or 0)
            if key in cells:                     # double gameweek: accumulate
                pts += cells[key][0]
                home, oc, gs = cells[key][1], cells[key][2], cells[key][3]
                pos, o3 = cells[key][4], cells[key][5]
                nm = cells[key][6] + 1
                mins += cells[key][7]
            cells[key] = (pts, home, oc, gs, pos, o3, nm, mins)
            apps[code] = apps.get(code, 0) + 1
            pos_of[code] = pos

    current = {str(e["code"]): e for e in d["elements"]}
    keep = sorted(c for c in apps
                  if apps[c] >= min_career_apps or c in current)
    keep += sorted(c for c in current if c not in set(keep))
    keep = list(dict.fromkeys(keep))
    row_of = {c: i for i, c in enumerate(keep)}
    N = len(keep)

    Y = np.zeros((N, T))
    D = np.zeros((N, T))
    qf = N_FIXTURE_X[fixture_mode]
    q = qf + (3 if use_odds else 0)
    X = np.zeros((N, T, q))
    if use_odds:
        X[:, :, qf] = BASELINE["p_win"]
        X[:, :, qf + 1] = BASELINE["p_opp_win"]
        X[:, :, qf + 2] = BASELINE["p_over"]
    M = np.zeros((N, T))
    MINS = np.zeros((N, T))
    for (code, col), (pts, home, oc, gs, pos, o3, nm, mins) in cells.items():
        i = row_of.get(code)
        if i is None:
            continue
        Y[i, col], D[i, col], M[i, col] = pts / nm, 1.0, float(nm)
        MINS[i, col] = float(mins)
        X[i, col, :qf] = fixture_x(fixture_mode, pos, home, oc, gs)
        if use_odds:
            X[i, col, qf], X[i, col, qf + 1], X[i, col, qf + 2] = o3
    if n_matched[1]:
        meta_match = n_matched[0] / n_matched[1]
    else:
        meta_match = 0.0

    # future columns: the fixture covariates for every remaining gameweek,
    # read off the published 26/27 fixture list. This is what lets the
    # forecast differ across a player's own next five gameweeks rather
    # than being one flat per-match rate repeated.
    fx = json.load(open(f"{data_dir}/fixtures_2627.json"))
    tname = {t["id"]: t["name"] for t in d["teams"]}
    last = len(SEASONS) - 1
    fin_c = {tm: conceded[(last, tm, "final")]
             for (si2, tm, g) in conceded if si2 == last and g == "final"}
    fin_s = {tm: scored[(last, tm, "final")]
             for (si2, tm, g) in scored if si2 == last and g == "final"}
    prom_c = float(np.quantile(list(fin_c.values()), 0.8))
    prom_s = float(np.quantile(list(fin_s.values()), 0.2))
    ETYPE = {t["id"]: t["singular_name_short"] for t in d["element_types"]}
    sched = {}                                    # (team_id, gw) -> (home, opp)
    nsched = {}                                   # (team_id, gw) -> match count
    for f in fx:
        if f["event"] is None:
            continue
        gw = f["event"] - 1
        sched.setdefault((f["team_h"], gw), (1.0, f["team_a"]))
        sched.setdefault((f["team_a"], gw), (0.0, f["team_h"]))
        nsched[(f["team_h"], gw)] = nsched.get((f["team_h"], gw), 0) + 1
        nsched[(f["team_a"], gw)] = nsched.get((f["team_a"], gw), 0) + 1
    live_si = len(SEASONS)
    for code, e in current.items():
        i = row_of[code]
        # the draft API's classification governs which slope applies, and
        # it is a season fresher than the archive's
        pos = ETYPE.get(e["element_type"]) or pos_of.get(code, "MID")
        if pos not in POS_GROUPS:
            pos = "MID"
        for gw in range(38):
            col = n_hist * 38 + gw
            if D[i, col] > 0:
                continue                       # already observed live cell
            ho = sched.get((e["team"], gw))
            if ho is not None:
                oname = tname.get(ho[1], "")
                oc = conceded.get((live_si, oname, gw),
                                  fin_c.get(oname, prom_c))
                gs = scored.get((live_si, oname, gw),
                                fin_s.get(oname, prom_s))
                X[i, col, :qf] = fixture_x(fixture_mode, pos, ho[0], oc, gs)
                M[i, col] = float(nsched.get((e["team"], gw), 1))

    meta = dict(codes=keep, row_of=row_of, pos_of=pos_of,
                current=set(current), n_hist=n_hist,
                odds_match_rate=meta_match, M=M, MINS=MINS,
                fixture_mode=fixture_mode, n_fixture_x=qf)
    return Y, D, X, season_of, gw_of, meta

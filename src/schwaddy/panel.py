"""Build the player-gameweek panel for TROP-forecast.

Rows: player code (stable across seasons). Columns: (season, gw), five
historical seasons plus the 38 future gameweeks of 2026/27.
Y: per-match points under 2026/27 DRAFT scoring recomputed from raw match
stats (DefCon fields exist only from 2025/26; earlier seasons score 0
there, partially absorbed by season effects).
D: 1 if the player played minutes that gameweek.
X: home dummy plus opponent rolling goals conceded (both observed for
future cells from the published fixture list; double gameweeks use the
first fixture, blanks are home=0 with D=0).
"""
import csv, json
import numpy as np

from .odds import load_historical, cell_covariates, BASELINE

SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
LIVE = "2026-27"


def _team_conceded(data_dir, prior_mode="team", prior_w=16.0):
    """(season_idx, team_name) -> per-GW rolling mean goals conceded
    entering each GW (season-to-date, shrunk toward a prior), plus final
    season mean per team.

    prior_mode "team" anchors each club to its own previous-season rate,
    falling back to a promoted-club prior for newcomers. "flat" is the old
    behaviour, a single league average for everyone, which discards what
    is already known about a club and leaves the covariate at the mercy of
    one match: measured on the live season, a single gameweek doubled the
    spread of the opponent term and left it correlated 0.27 with the
    settled rates it replaced.
    """
    import os
    conceded = {}
    live_list = [LIVE] if os.path.exists(f"{data_dir}/gws_{LIVE}.csv") else []
    prev_final = {}
    for si, s in enumerate(SEASONS + live_list):
        per_match = {}
        for r in csv.DictReader(open(f"{data_dir}/gws_{s}.csv")):
            gw = int(float(r["GW"])) - 1
            if not (0 <= gw < 38):
                continue
            hs, as_ = r["team_h_score"], r["team_a_score"]
            if hs in ("", None) or as_ in ("", None):
                continue
            gc = int(float(as_)) if r["was_home"] in ("True", "TRUE", "1") \
                else int(float(hs))
            per_match.setdefault((r["team"], gw), gc)
        teams = sorted({k[0] for k in per_match})
        # newcomers get the 80th percentile of last season's rates: a
        # promoted side concedes like a poor one, not like the average
        promoted = (float(np.quantile(list(prev_final.values()), 0.8))
                    if prev_final else 1.4)
        for tm in teams:
            if prior_mode == "team":
                prior = prev_final.get(tm, promoted)
            else:
                prior = 1.4
            run, n = 0.0, 0
            for gw in range(38):
                conceded[(si, tm, gw)] = (run + prior_w * prior) / (n + prior_w)
                if (tm, gw) in per_match:
                    run += per_match[(tm, gw)]
                    n += 1
            conceded[(si, tm, "final")] = run / max(n, 1)
        prev_final = {tm: conceded[(si, tm, "final")] for tm in teams}
    return conceded


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
          conceded_prior="team", conceded_w=16.0):
    d = json.load(open(f"{data_dir}/draft_bootstrap.json"))
    S = d["settings"]["scoring"]
    POS = {t["id"]: t["singular_name_short"] for t in d["element_types"]}

    n_hist = len(SEASONS)
    T = (n_hist + 1) * 38
    season_of = np.repeat(np.arange(n_hist + 1), 38)
    gw_of = np.tile(np.arange(38), n_hist + 1)

    conceded = _team_conceded(data_dir, prior_mode=conceded_prior,
                              prior_w=conceded_w)
    odds = load_historical(data_dir, SEASONS) if use_odds else {}
    n_matched = [0, 0]
    cells = {}          # (code, col) -> (points, home, opp_conceded, odds3,
                        #                 n_matches, minutes)
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
            oc = conceded.get((si, opp, gw), 1.4)
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
                home, oc, o3 = cells[key][1], cells[key][2], cells[key][3]
                nm = cells[key][4] + 1
                mins += cells[key][5]
            cells[key] = (pts, home, oc, o3, nm, mins)
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
    q = 5 if use_odds else 2
    X = np.zeros((N, T, q))
    if use_odds:
        X[:, :, 2] = BASELINE["p_win"]
        X[:, :, 3] = BASELINE["p_opp_win"]
        X[:, :, 4] = BASELINE["p_over"]
    M = np.zeros((N, T))
    MINS = np.zeros((N, T))
    for (code, col), (pts, home, oc, o3, nm, mins) in cells.items():
        i = row_of.get(code)
        if i is None:
            continue
        Y[i, col], D[i, col], M[i, col] = pts / nm, 1.0, float(nm)
        MINS[i, col] = float(mins)
        X[i, col, 0], X[i, col, 1] = home, oc
        if use_odds:
            X[i, col, 2], X[i, col, 3], X[i, col, 4] = o3
    if n_matched[1]:
        meta_match = n_matched[0] / n_matched[1]
    else:
        meta_match = 0.0

    # future columns: home dummy from the published 26/27 fixture list
    fx = json.load(open(f"{data_dir}/fixtures_2627.json"))
    tname = {t["id"]: t["name"] for t in d["teams"]}
    last = len(SEASONS) - 1
    finals = {tm: conceded[(last, tm, "final")]
              for (si2, tm, g) in conceded if si2 == last and g == "final"}
    promoted_prior = float(np.quantile(list(finals.values()), 0.8))
    def opp_oc(tid):
        return finals.get(tname.get(tid, ""), promoted_prior)
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
        for gw in range(38):
            col = n_hist * 38 + gw
            if D[i, col] > 0:
                continue                       # already observed live cell
            ho = sched.get((e["team"], gw))
            if ho is not None:
                X[i, col, 0] = ho[0]
                oname = tname.get(ho[1], "")
                X[i, col, 1] = conceded.get((live_si, oname, gw),
                                            opp_oc(ho[1]))
                M[i, col] = float(nsched.get((e["team"], gw), 1))

    meta = dict(codes=keep, row_of=row_of, pos_of=pos_of,
                current=set(current), n_hist=n_hist,
                odds_match_rate=meta_match, M=M, MINS=MINS)
    return Y, D, X, season_of, gw_of, meta

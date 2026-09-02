"""Pipeline entrypoint: pull data, build panel, fit, write predictions.json.

Run as: python -m schwaddy.refresh [--cv] [--news-only] [--league-id N]
CV is expensive, so the cron run reuses the last selected lambdas from
data/predictions.json unless --cv is passed (recommended weekly).

--news-only skips the panel build and refit entirely and just refreshes
data/news.json. The night cron uses it to post the matchday recap once
the day's matches have wrapped up, reusing the morning run's forecasts.
"""
import argparse, csv, io, json, os, sys
import numpy as np
import requests

from . import (api, compare, depth, liveform, livegws, overrides,
               playerstats, prices, weekly)
from .panel import build, SEASONS, LIVE, POS_GROUPS
from .mc import TropForecast
from .lineup import p_plays, pick_xi, waiver_claims
from .availability import availability_path

RAW = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"

# gameweeks in the short-horizon view. Five is the span a waiver claim is
# judged over, and the span over which the fixture list still differs
# between clubs.
HORIZON = 5


def pull(data_dir):
    json.dump(api.draft_bootstrap(), open(f"{data_dir}/draft_bootstrap.json", "w"))
    json.dump(api.fixtures(), open(f"{data_dir}/fixtures_2627.json", "w"))
    live_archive = False
    for s in SEASONS + [LIVE]:
        for f in ("gws/merged_gw.csv", "players_raw.csv", "teams.csv"):
            out = f"{data_dir}/{f.split('/')[-1].replace('.csv', '')}_{s}.csv" \
                if "merged" not in f else f"{data_dir}/gws_{s}.csv"
            # historical files are immutable; live-season files grow, so
            # always re-download them
            if s != LIVE and os.path.exists(out):
                continue
            r = requests.get(f"{RAW}/{s}/{f}", timeout=60)
            if r.status_code != 200:
                if s == LIVE:
                    continue                   # archive not started yet
                r.raise_for_status()
            open(out, "w").write(r.text)
            if s == LIVE and "merged" in f:
                live_archive = True       # the real archive has caught up
    if not live_archive:
        # the archive lags the live season by weeks; rebuild it from the API
        # so the fit is not stuck on last season while squads have changed
        try:
            fx = json.load(open(f"{data_dir}/fixtures_2627.json"))
            n = livegws.write(data_dir, fx)
            print(f"live gameweeks reconstructed from the API: {n} rows")
        except Exception as ex:
            print(f"live gameweek reconstruction skipped: {ex}")


def _weekly(data_dir, league_id, bootstrap, owned, id_of_code):
    """Live weekly state for the dashboard, and for the news feed to quote.

    Never fatal: the feed falls back to computing its own live table from
    the standings, which is the behaviour that predates this file.
    """
    try:
        st = weekly.write(data_dir, league_id, bootstrap, owned, id_of_code)
    except Exception as ex:
        print(f"weekly league state skipped: {ex}")
        return None
    if st:
        print(f"weekly league state: GW{st['gw']}, "
              f"{len(st['managers'])} managers, "
              f"{sum(m['to_play'] for m in st['managers'])} players to play")
    return st


def _player_stats(data_dir, bootstrap):
    """Season stats and match logs behind the dashboard's player card.

    Never fatal: the card falls back to the projections alone, which come
    from predictions.json and are already on the page.
    """
    try:
        st = playerstats.write(data_dir, bootstrap)
    except Exception as ex:
        print(f"player stats skipped: {ex}")
        return
    logged = sum(1 for p in st["players"].values() if p["log"])
    print(f"player stats: {len(st['players'])} players, "
          f"{logged} with a {st['season']} match log")


def _prices(data_dir):
    """Classic-game prices behind the Waivers tab's market table.

    Never fatal: the site renders the price columns blank without it.
    """
    try:
        out = prices.write(data_dir)
    except Exception as ex:
        print(f"prices skipped: {ex}")
        return
    print(f"prices: {len(out['players'])} players, GW{out['gw']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cv", action="store_true")
    ap.add_argument("--news-only", action="store_true",
                    help="refresh data/news.json without refitting the model")
    ap.add_argument("--league-id", type=int, default=9450)
    ap.add_argument("--data-dir", default="../data")
    args = ap.parse_args()
    os.makedirs(args.data_dir, exist_ok=True)

    if args.news_only:
        # matchday run: current flags and ownership are all the feed needs,
        # and it reads the morning run's predictions.json for projections.
        from . import news
        d = api.draft_bootstrap()      # read in memory; pull() commits it daily
        for w in overrides.apply(d):
            print(f"override: {w}")
        owned = {}
        if args.league_id:
            es = api.element_status(args.league_id)
            owned = {s["element"]: s["owner"] for s in es["element_status"]
                     if s.get("owner")}
        id_of_code = {str(e["code"]): e["id"] for e in d["elements"]}
        wk = _weekly(args.data_dir, args.league_id, d, owned, id_of_code)
        n_new = news.update(args.data_dir, args.league_id, d, owned,
                            id_of_code, weekly=wk)
        print(f"news feed updated: {n_new} new events")
        _player_stats(args.data_dir, d)
        _prices(args.data_dir)
        return

    pull(args.data_dir)

    Y, D, X, season_of, gw_of, meta = build(args.data_dir)
    prev = {}
    pj = f"{args.data_dir}/predictions.json"
    if os.path.exists(pj):
        prev = json.load(open(pj))
    pos_idx = {i: meta["pos_of"].get(c, "MID")
               for i, c in enumerate(meta["codes"])}
    POSN = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
    groups = np.array([POSN[pos_idx[i]] for i in range(Y.shape[0])])
    m = TropForecast(ridge_unit=15.0, unit_groups=groups)
    if args.cv or "lambda_time" not in prev:
        m.cv_utility(Y, D, season_of, gw_of, pos_idx, X=X,
                     time_grid=(0.0, 0.01, 0.03, 0.06),
                     nn_grid=(2.0, 5.0, 10.0, np.inf),
                     cv_holdout=6, n_blocks=3)
    else:
        m.lambda_time = prev["lambda_time"]
        m.lambda_nn = prev["lambda_nn"] if prev["lambda_nn"] else np.inf
    m.fit(Y, D, season_of, gw_of, X=X)

    d = json.load(open(f"{args.data_dir}/draft_bootstrap.json"))
    for w in overrides.apply(d):       # transfers the API has not posted yet
        print(f"override: {w}")
    info = {str(e["code"]): e for e in d["elements"]}
    n_hist = meta["n_hist"]
    obs_cols = np.where(D.any(axis=0))[0]
    nxt = int(obs_cols.max()) + 1              # first unplayed column
    lo = max(0, nxt - 8)
    # remaining-GW first-kickoff dates and last-season club per player
    from datetime import date as _date
    fx = json.load(open(f"{args.data_dir}/fixtures_2627.json"))
    gw_first = {}
    for f in fx:
        if f["event"] is None or not f.get("kickoff_time"):
            continue
        d0 = _date.fromisoformat(f["kickoff_time"][:10])
        g = f["event"] - 1
        gw_first[g] = min(gw_first.get(g, d0), d0)
    first_future_gw = max(0, nxt - n_hist * 38)
    try:
        from . import api as _api
        game = _api.get("https://draft.premierleague.com/api/game")
        nx = game["next_event"] - 1
        if game.get("current_event_finished") is False and game.get("current_event"):
            pass                       # next_event already points past live GW
        first_future_gw = max(first_future_gw, nx)
    except Exception:
        pass                           # archive-derived fallback stands
    gw_dates = [gw_first.get(g, _date(2027, 6, 1))
                for g in range(first_future_gw, 38)]
    import csv as _csv
    last_s = SEASONS[-1]
    code_of_prev = {r2["id"]: r2["code"] for r2 in
                    _csv.DictReader(open(f"{args.data_dir}/players_raw_{last_s}.csv"))}
    prev_club = {}
    for r2 in _csv.DictReader(open(f"{args.data_dir}/gws_{last_s}.csv")):
        c2 = code_of_prev.get(r2["element"])
        if c2:
            prev_club[c2] = r2["team"]
    tname = {t["id"]: t["name"] for t in d["teams"]}
    ETYPE = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
    avail_of = {}
    id_of_code = {str(e["code"]): e["id"] for e in d["elements"]}
    # the archive lags the live season by weeks, so take this season's
    # minutes from the draft API rather than waiting for it
    live_gws, live_mins, live_matches = liveform.load(args.data_dir)
    print(f"live minutes: {len(live_gws)} gameweeks, "
          f"{len(live_mins)} players")
    # every minutes share first: club depth needs the whole group before it
    # can hand a flagged player's minutes to the team-mates who are fit
    shares = {}
    for i, code in enumerate(meta["codes"]):
        if code not in meta["current"]:
            continue
        e = info[code]
        shares[e["id"]] = liveform.trailing_share(
            meta["MINS"][i], meta["M"][i], D[i], n_hist, e["team"], live_gws,
            live_mins.get(id_of_code.get(code), {}), live_matches)
    boost = depth.multipliers(d["elements"], shares)
    print(f"depth: {len(boost)} players absorbing minutes from flagged "
          f"team-mates")

    # ---- the next-HORIZON gameweeks -------------------------------------
    # Rest-of-season totals wash the fixture list out: every club plays
    # everybody eventually, so over 38 gameweeks the schedule is nearly a
    # constant and only the player differs. Over five it is not, and five
    # is the horizon a waiver claim is actually judged on. Everything the
    # site ranks on used to be "rest", which is why a good or bad run was
    # invisible in the product even though the panel carried it.
    #
    # Measured rolling-origin over 23/24, 24/25 and 25/26: rank the whole
    # league at each origin gameweek, then score what those players went
    # on to make over the following five gameweeks.
    #
    #                rank rho vs realized      top-20 shortlist, realized pts
    #   next5          .653  .694  .618          22.77  23.64  21.37
    #   rest (old)     .600  .642  .556          22.11  22.74  20.54
    #   next GW x5     .553  .593  .541          21.37  22.01  19.80
    #
    # Three seasons out of three, t = 12.5 to 13.8 on the correlation and
    # 2.8 to 4.1 on the realized points. The third row matters too: simply
    # shortening the horizon is not what does it, because repeating one
    # gameweek five times is the WORST of the three. It is the schedule.
    tshort = {t["id"]: t["short_name"] for t in d["teams"]}
    hz = range(first_future_gw, min(38, first_future_gw + HORIZON))
    opp_of = {}                      # (team id, gw) -> [opponent label]
    for f in fx:
        if f["event"] is None:
            continue
        g = f["event"] - 1
        if g not in hz:
            continue
        # upper case = home, lower case = away, matching the site's ticker
        opp_of.setdefault((f["team_h"], g), []).append(
            tshort.get(f["team_a"], "?").upper())
        opp_of.setdefault((f["team_a"], g), []).append(
            tshort.get(f["team_h"], "?").lower())
    qf = meta["n_fixture_x"]
    beta = m.beta_[:qf] * m.y_sd_ if len(m.beta_) >= qf else np.zeros(qf)

    def _fixture_raw(i):
        """The model's fixture term over the horizon, in points."""
        tot = 0.0
        for g in hz:
            col = n_hist * 38 + g
            if meta["M"][i, col] <= 0:
                continue               # blank gameweek: nothing to judge
            tot += float(X[i, col, :qf] @ beta)
        return tot

    # A run is only kind or cruel relative to what everyone else at the
    # same position faces in the same five weeks, so the reference point
    # is that group's mean rather than any absolute zero. Centring the
    # covariates is not enough on its own: the league's average opponent
    # is not exactly the centring constant in any given five-week window,
    # and the
    # residual shows up as an offset that differs by position.
    fix_raw, fix_mean = {}, {}
    for i, code in enumerate(meta["codes"]):
        if code in meta["current"]:
            fix_raw[i] = _fixture_raw(i)
    for grp in POS_GROUPS:
        vals = [fix_raw[i] for i, c in enumerate(meta["codes"])
                if i in fix_raw
                and (ETYPE.get(info[c]["element_type"]) or "MID") == grp]
        fix_mean[grp] = float(np.mean(vals)) if vals else 0.0

    def fixture_points(i, pos):
        if i not in fix_raw:
            return None
        return round(fix_raw[i] - fix_mean.get(pos, 0.0), 2)

    players = []
    for i, code in enumerate(meta["codes"]):
        if code not in meta["current"]:
            continue
        e = info[code]
        share = shares.get(e["id"])
        if share is not None:
            share = min(1.0, share * boost.get(e["id"], 1.0))
        base = p_plays(D[i, lo:nxt], "a", None, share)   # healthy-state level
        new_club = prev_club.get(code) is None \
            or prev_club.get(code) != tname[e["team"]]
        # only discount while the trailing window still lacks new-club minutes
        if D[i, n_hist * 38:nxt].sum() >= 4:
            new_club = False
        path = availability_path(e["status"],
                                 e.get("chance_of_playing_next_round"),
                                 e.get("news"), base, gw_dates,
                                 new_club=new_club)
        avail = path[0]
        avail_of[code] = avail        # reused by the rival-prediction table
        Mrow = meta["M"][i, n_hist * 38 + first_future_gw:]
        pv = m.pred_[i, n_hist * 38 + first_future_gw:]
        per_gw = np.concatenate([np.zeros(first_future_gw),
                                 (pv * np.array(path) * Mrow)]).round(2)
        pos = ETYPE.get(e["element_type"]) or meta["pos_of"].get(code, "MID")
        players.append(dict(
            code=code, n_career=int(D[i].sum()),
            name=e["web_name"],
            # the draft API's classification governs squad and XI legality,
            # so it wins over the archive's, which can be a season stale
            pos=pos,
            team=e["team"], status=e["status"], news=(e.get("news") or "")[:90],
            avail=round(avail, 2), gw=per_gw.tolist(),
            rest=float(per_gw.sum()),
            next5=round(float(per_gw[first_future_gw:
                                     first_future_gw + HORIZON].sum()), 2),
            fix5=fixture_points(i, pos),
            run=["+".join(opp_of[(e["team"], g)])
                 if (e["team"], g) in opp_of else "-" for g in hz]))
    owned = {}
    if args.league_id:
        es = api.element_status(args.league_id)
        owned = {s["element"]: s["owner"] for s in es["element_status"]
                 if s.get("owner")}
    from .league import MY_ENTRY
    for p in players:
        pid = id_of_code.get(p["code"])
        p["owner"] = owned.get(pid)
        p["mine"] = owned.get(pid) == MY_ENTRY
    out = dict(generated=str(np.datetime64("now")),
               horizon=HORIZON, first_gw=first_future_gw + 1,
               lambda_time=float(m.lambda_time),
               lambda_nn=(None if not np.isfinite(m.lambda_nn)
                          else float(m.lambda_nn)),
               owned={str(k): v for k, v in owned.items()},
               players=sorted(players, key=lambda p: -p["rest"]))
    json.dump(out, open(pj, "w"))
    print(f"wrote {pj}: {len(players)} players, "
          f"lambdas=({m.lambda_time}, {m.lambda_nn})")
    kind = sorted((p for p in players if p["fix5"] is not None),
                  key=lambda p: -p["fix5"])
    if kind:
        print(f"horizon: GW{first_future_gw + 1}-{min(38, first_future_gw + HORIZON)}"
              f", fixture term {kind[-1]['fix5']:+.2f} to {kind[0]['fix5']:+.2f} pts"
              f" (kindest run {kind[0]['name']}, hardest {kind[-1]['name']})")
    try:
        cmp_ = compare.write(args.data_dir, Y, D, meta, first_future_gw,
                             avail_of)
        if cmp_:
            print(f"rival predictions: {len(cmp_['players'])} players, "
                  f"{cmp_['n_fpl']} with an FPL ep_next, "
                  f"rank correlation {cmp_['spread']}")
    except Exception as ex:
        print(f"rival predictions skipped: {ex}")
    _player_stats(args.data_dir, d)
    _prices(args.data_dir)
    try:
        from . import news
        wk = _weekly(args.data_dir, args.league_id, d, owned, id_of_code)
        n_new = news.update(args.data_dir, args.league_id, d, owned,
                            id_of_code, weekly=wk)
        print(f"news feed updated: {n_new} new events")
    except Exception as ex:
        print(f"news update skipped: {ex}")


if __name__ == "__main__":
    main()

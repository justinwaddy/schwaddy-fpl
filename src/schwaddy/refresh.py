"""Pipeline entrypoint: pull data, build panel, fit, write predictions.json.

Run as: python -m schwaddy.refresh [--cv] [--league-id N]
CV is expensive, so the cron run reuses the last selected lambdas from
data/predictions.json unless --cv is passed (recommended weekly).
"""
import argparse, csv, io, json, os, sys
import numpy as np
import requests

from . import api
from .panel import build, SEASONS, LIVE
from .mc import TropForecast
from .lineup import p_plays, pick_xi, waiver_claims
from .availability import availability_path

RAW = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"


def pull(data_dir):
    json.dump(api.draft_bootstrap(), open(f"{data_dir}/draft_bootstrap.json", "w"))
    json.dump(api.fixtures(), open(f"{data_dir}/fixtures_2627.json", "w"))
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cv", action="store_true")
    ap.add_argument("--league-id", type=int, default=9450)
    ap.add_argument("--data-dir", default="../data")
    args = ap.parse_args()
    os.makedirs(args.data_dir, exist_ok=True)
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
    players = []
    for i, code in enumerate(meta["codes"]):
        if code not in meta["current"]:
            continue
        e = info[code]
        base = p_plays(D[i, lo:nxt], "a", None)   # healthy-state level
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
        Mrow = meta["M"][i, n_hist * 38 + first_future_gw:]
        pv = m.pred_[i, n_hist * 38 + first_future_gw:]
        per_gw = np.concatenate([np.zeros(first_future_gw),
                                 (pv * np.array(path) * Mrow)]).round(2)
        players.append(dict(
            code=code, n_career=int(D[i].sum()),
            name=e["web_name"],
            pos=meta["pos_of"].get(code) or ETYPE.get(e["element_type"], "MID"),
            team=e["team"], status=e["status"], news=(e.get("news") or "")[:90],
            avail=round(avail, 2), gw=per_gw.tolist(),
            rest=float(per_gw.sum())))
    owned = {}
    if args.league_id:
        es = api.element_status(args.league_id)
        owned = {s["element"]: s["owner"] for s in es["element_status"]
                 if s.get("owner")}
    from .league import MY_ENTRY
    id_of_code = {}
    for e in d["elements"]:
        id_of_code[str(e["code"])] = e["id"]
    for p in players:
        pid = id_of_code.get(p["code"])
        p["owner"] = owned.get(pid)
        p["mine"] = owned.get(pid) == MY_ENTRY
    out = dict(generated=str(np.datetime64("now")),
               lambda_time=float(m.lambda_time),
               lambda_nn=(None if not np.isfinite(m.lambda_nn)
                          else float(m.lambda_nn)),
               owned={str(k): v for k, v in owned.items()},
               players=sorted(players, key=lambda p: -p["rest"]))
    json.dump(out, open(pj, "w"))
    print(f"wrote {pj}: {len(players)} players, "
          f"lambdas=({m.lambda_time}, {m.lambda_nn})")
    try:
        from . import news
        n_new = news.update(args.data_dir, args.league_id, d,
                            owned, id_of_code)
        print(f"news feed updated: {n_new} new events")
    except Exception as ex:
        print(f"news update skipped: {ex}")


if __name__ == "__main__":
    main()

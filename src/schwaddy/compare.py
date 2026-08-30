"""Rival points predictions, written to data/compare.json.

The dashboard shows one number per player and no way to tell whether it is
any good. This puts three alternatives beside it for the coming gameweek:

  trop  the model in refresh.py, availability already applied
  fpl   FPL's own ep_next, straight from the classic API
  b1    season to date, shrunk toward last season (backtest.py's B1)
  b2    the mean of the player's last four played matches (B2)

B1 and B2 are per-appearance rates, so they are multiplied by the same
availability path the model uses; otherwise they would flatter anyone who
scores well when he plays and rarely plays.

One caveat sits over the FPL column and is repeated on the page: ep_next
is computed under classic scoring, not this league's draft scoring. They
share most of their table but not all of it - a keeper's goal is 10 here
against 6 there, and the two disagree on a few other lines - so treat it
as a good outside opinion rather than a like-for-like number.
"""
import json
from datetime import datetime, timezone

import numpy as np

from . import api
from .backtest import benchmarks


def _fpl_ep(cache=None):
    """{player code: ep_next} from the classic API, empty if unavailable."""
    try:
        boot = cache if cache is not None else api.classic_bootstrap()
    except Exception:
        return {}
    out = {}
    for e in boot.get("elements", []):
        v = e.get("ep_next")
        try:
            if v not in (None, ""):
                out[str(e["code"])] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def build(data_dir, Y, D, meta, first_future_gw, avail_path, classic=None):
    """Rows comparing the four predictors for the coming gameweek.

    avail_path: {code: availability for the coming gameweek}, as computed
    by refresh.py, so every column is discounted the same way.
    """
    codes = meta["codes"]
    n_hist = meta["n_hist"]
    base = n_hist * 38
    col = base + first_future_gw
    pos_idx = {i: meta["pos_of"].get(c, "MID") for i, c in enumerate(codes)}
    b1, b2 = benchmarks(Y, D, pos_idx, col, base=base)
    M = meta["M"]
    fpl = _fpl_ep(classic)
    try:
        pj = json.load(open(f"{data_dir}/predictions.json"))
    except Exception:
        return None
    rows = []
    for p in pj.get("players", []):
        code = p.get("code")
        if code not in set(codes):
            continue
        i = codes.index(code)
        gw = p.get("gw") or []
        trop = gw[first_future_gw] if len(gw) > first_future_gw else 0.0
        av = avail_path.get(code, p.get("avail", 1.0)) or 0.0
        nm = float(M[i, col]) if col < M.shape[1] else 1.0
        rows.append(dict(
            code=code, name=p["name"], pos=p["pos"], team=p.get("team"),
            owner=p.get("owner"), mine=bool(p.get("mine")), avail=round(av, 2),
            trop=round(float(trop), 2),
            fpl=(round(fpl[code], 2) if code in fpl else None),
            b1=round(float(b1[i]) * av * max(nm, 1.0), 2),
            b2=round(float(b2[i]) * av * max(nm, 1.0), 2)))
    rows.sort(key=lambda r: -r["trop"])
    return dict(
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
        gw=first_future_gw + 1, n_fpl=sum(1 for r in rows if r["fpl"] is not None),
        players=rows, spread=_spread(rows))


def _spread(rows):
    """Where the four predictors disagree most, and how they correlate."""
    def rank(key):
        vals = [(r["code"], r[key]) for r in rows if r[key] is not None]
        vals.sort(key=lambda x: -x[1])
        return {c: i for i, (c, _) in enumerate(vals)}
    out = {}
    rt = rank("trop")
    for k in ("fpl", "b1", "b2"):
        rk = rank(k)
        both = [c for c in rt if c in rk]
        if len(both) > 10:
            a = np.array([rt[c] for c in both], float)
            b = np.array([rk[c] for c in both], float)
            out[k] = dict(n=len(both),
                          rho=round(float(np.corrcoef(a, b)[0, 1]), 3))
    return out


def write(data_dir, Y, D, meta, first_future_gw, avail_path, classic=None):
    """Write data/compare.json. Returns the state, or None."""
    state = build(data_dir, Y, D, meta, first_future_gw, avail_path, classic)
    if not state:
        return None
    json.dump(state, open(f"{data_dir}/compare.json", "w"))
    return state

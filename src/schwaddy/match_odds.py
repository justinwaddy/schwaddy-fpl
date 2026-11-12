"""Match-winner odds for the Live tab.

Once a day (the 09:35 UK morning refresh) this pulls the Premier League
1X2 and over/under markets, strips the bookmaker's margin out, and writes
data/match_odds.json keyed by FPL fixture id.

Source: ESPN's public scoreboard feed, which carries DraftKings' current
lines for every fixture and needs no key or account. If ODDS_API_KEY is
set (the-odds-api.com, free tier) that is used instead, since it averages
every UK bookmaker rather than one; ESPN is the fallback if it fails.


    {"generated": "...Z", "source": "the-odds-api.com",
     "fixtures": {"123": {"h": 1, "a": 6, "ko": "...Z",
                          "ph": 0.52, "pd": 0.25, "pa": 0.23,
                          "oh": 1.85, "od": 3.6, "oa": 4.0,
                          "books": 14, "pulled": "...Z"}}}

ph/pd/pa are the de-vigged chances of a home win, draw and away win;
oh/od/oa the average decimal prices they came from. po is the chance of
over `ln` goals (usually 2.5) from the totals market, when one is posted.

Scorelines: the bookies' correct-score market is not in this feed, so
the scores are derived. A Dixon-Coles model (Poisson goals for each side,
with the usual correction for 0-0, 1-0, 0-1 and 1-1) is fitted to each
match's three numbers - home win, draw, over the line - which pins its three
parameters: each side's expected goals and the low-score correction.
"sc" then holds every scoreline up to 6-6 with at least a 0.5% chance,
as {"h-a": p}; xh/xa are the fitted expected goals and csh/csa each
side's chance of a clean sheet (the other side scoring none). These are fair
chances from the 1X2 and totals lines, not a bookmaker's quoted prices. A fixture keeps the
last line pulled before it kicked off: once it has started the bookies
only post in-play prices, which are not what the tab is showing, so a
started fixture is never overwritten.

With ODDS_API_KEY: the free tier is 500 requests a month and a pull
costs two (1X2 and totals), so a pull a day is well under it.

    python -m schwaddy.match_odds --data-dir ../data
"""
import argparse, csv, json, os, re, sys
from datetime import datetime, timezone

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

from .odds import fetch_live

# the-odds-api name -> FPL name, where normalising alone does not get there
ALIASES = {
    "manchester united": "man utd", "manchester city": "man city",
    "tottenham hotspur": "spurs", "nottingham forest": "nott'm forest",
    "newcastle united": "newcastle", "brighton and hove albion": "brighton",
    "wolverhampton wanderers": "wolves", "west ham united": "west ham",
    "leeds united": "leeds", "afc bournemouth": "bournemouth",
    "leicester city": "leicester", "sheffield united": "sheffield utd",
    "luton town": "luton", "ipswich town": "ipswich town",
    "coventry city": "coventry city", "hull city": "hull city",
}


def _norm(s):
    s = s.lower().replace("&", "and")
    s = re.sub(r"\b(fc|afc)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def team_lookup(data_dir, season="2026-27"):
    """Normalised name (and short name) -> FPL team id."""
    out = {}
    for r in csv.DictReader(open(f"{data_dir}/teams_{season}.csv",
                                 encoding="utf-8")):
        tid = int(r["id"])
        out[_norm(r["name"])] = tid
        out[r["short_name"].lower()] = tid
    return out


def resolve(name, lookup):
    n = _norm(name)
    n = ALIASES.get(n, n)
    if n in lookup:
        return lookup[n]
    # "Hull City" vs "Hull", "Ipswich Town" vs "Ipswich": take a unique
    # FPL name that is a whole-word prefix of the bookies' one or vice versa
    hits = {tid for k, tid in lookup.items() if len(k) > 3 and
            (n.startswith(k + " ") or k.startswith(n + " "))}
    return hits.pop() if len(hits) == 1 else None


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def match_events(events, fixtures, lookup):
    """Pair each bookmaker event with an FPL fixture: same two teams,
    kick-off within a day of each other (FPL and the bookies disagree by
    minutes now and then, and a rearranged game moves both)."""
    by_teams = {}
    for f in fixtures:
        if f.get("kickoff_time"):
            by_teams.setdefault((f["team_h"], f["team_a"]), []).append(f)
    out, missed = [], []
    for ev in events:
        h, a = resolve(ev["home_team"], lookup), resolve(ev["away_team"], lookup)
        cands = by_teams.get((h, a), [])
        ko = _ts(ev["commence_time"])
        cands = [f for f in cands
                 if abs((_ts(f["kickoff_time"]) - ko).total_seconds()) < 86400]
        if len(cands) == 1:
            out.append((cands[0], ev))
        else:
            missed.append(f'{ev["home_team"]} v {ev["away_team"]}')
    return out, missed


def price(ev):
    """Average 1X2 price across books, de-vigged; None if no book has it."""
    hs, ds, as_ = [], [], []
    for bk in ev.get("bookmakers", []):
        for mk in bk.get("markets", []):
            if mk.get("key") != "h2h":
                continue
            o = {x["name"]: x["price"] for x in mk.get("outcomes", [])}
            h, a, d = o.get(ev["home_team"]), o.get(ev["away_team"]), o.get("Draw")
            if h and a and d:
                hs.append(h); ds.append(d); as_.append(a)
    if not hs:
        return None
    oh, od, oa = (sum(x) / len(x) for x in (hs, ds, as_))
    inv = 1 / oh + 1 / od + 1 / oa
    return dict(ph=round(1 / oh / inv, 3), pd=round(1 / od / inv, 3),
                pa=round(1 / oa / inv, 3), oh=round(oh, 2), od=round(od, 2),
                oa=round(oa, 2), books=len(hs))


MAXG = 10                                   # grid size for the sums; 6-6 shown


def _grid(lh, la, rho):
    g = np.outer(poisson.pmf(np.arange(MAXG + 1), lh),
                 poisson.pmf(np.arange(MAXG + 1), la))
    g[0, 0] *= 1 - lh * la * rho
    g[0, 1] *= 1 + lh * rho
    g[1, 0] *= 1 + la * rho
    g[1, 1] *= 1 - rho
    return g / g.sum()


def _summary(g, line=2.5):
    n = np.arange(MAXG + 1)
    tot = n[:, None] + n[None, :]
    return (np.tril(g, -1).sum(), np.trace(g), g[tot > line].sum())


def fit_scores(ph, pd, pa, po=None, line=2.5):
    """Dixon-Coles (lh, la, rho) matching home/draw/(over) - exact when the
    totals line is there, closest fit on the 1X2 alone otherwise."""
    def loss(x):
        lh, la, rho = np.exp(x[0]), np.exp(x[1]), x[2]
        h, d, o = _summary(_grid(lh, la, rho), line)
        e = (h - ph) ** 2 + (d - pd) ** 2 + (1 - h - d - pa) ** 2
        if po is not None:
            e += (o - po) ** 2
        else:
            e += 1e-3 * (rho + 0.05) ** 2      # no totals: keep rho typical
        return e
    best = min((minimize(loss, x0, method="Nelder-Mead",
                         options=dict(xatol=1e-6, fatol=1e-12, maxiter=4000))
                for x0 in ([0.4, 0.1, -0.05], [0.1, 0.4, -0.05])),
               key=lambda r: r.fun)
    lh, la = np.exp(best.x[0]), np.exp(best.x[1])
    rho = float(np.clip(best.x[2], -0.3, 0.3))
    g = _grid(lh, la, rho)
    sc = {f"{i}-{j}": round(float(g[i, j]), 4)
          for i in range(7) for j in range(7) if g[i, j] >= 0.005}
    # clean sheets: home keeps one when the away side scores none, and back
    return dict(xh=round(float(lh), 2), xa=round(float(la), 2),
                csh=round(float(g[:, 0].sum()), 3),
                csa=round(float(g[0, :].sum()), 3), sc=sc)


def over_under(ev):
    """(de-vigged chance of over the line, the line), averaged across books
    on the 2.5 line when any book has it, else the commonest line posted;
    (None, None) without a totals market."""
    by = {}
    for bk in ev.get("bookmakers", []):
        for mk in bk.get("markets", []):
            if mk.get("key") != "totals":
                continue
            o = {(x["name"], x.get("point")): x["price"] for x in mk.get("outcomes", [])}
            for (nm, pt), ov in o.items():
                un = o.get(("Under", pt))
                if nm == "Over" and ov and un and pt is not None:
                    by.setdefault(pt, []).append((1 / ov) / (1 / ov + 1 / un))
    if not by:
        return None, None
    line = 2.5 if 2.5 in by else max(by, key=lambda k: len(by[k]))
    return round(sum(by[line]) / len(by[line]), 3), line


ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard"


def _american(s):
    """'+250' / '-600' -> decimal odds; None if absent or 'EVEN'-less junk."""
    if s in (None, "", "OFF"):
        return None
    s = str(s).strip().upper()
    if s == "EVEN":
        return 2.0
    try:
        v = float(s)
    except ValueError:
        return None
    if v == 0:
        return None
    return 1 + (v / 100 if v > 0 else 100 / -v)


def fetch_espn(days=14):
    """ESPN's scoreboard for the next `days`, reshaped into the-odds-api's
    event format so price() and over_under() read either. Uses the
    'close' line, which is the current one until kick-off."""
    import requests
    from datetime import timedelta
    d0 = datetime.now(timezone.utc)
    evs, seen = [], set()
    for k in range(days):                # the soccer feed takes one day at a time
        r = requests.get(ESPN, params=dict(dates=f"{d0 + timedelta(days=k):%Y%m%d}"),
                         timeout=30, headers={"User-Agent": "schwaddy-fpl"})
        r.raise_for_status()
        for e in r.json().get("events", []):
            if e.get("id") not in seen:
                seen.add(e.get("id")); evs.append(e)
    out = []
    for e in evs:
        c = (e.get("competitions") or [{}])[0]
        if (c.get("status", {}).get("type", {}).get("state")) != "pre":
            continue
        side = {t.get("homeAway"): t["team"]["displayName"]
                for t in c.get("competitors", [])}
        if "home" not in side or "away" not in side:
            continue
        bks = []
        for od in c.get("odds") or []:
            ml = od.get("moneyline") or {}
            cur = lambda k: _american(((ml.get(k) or {}).get("close") or
                                       (ml.get(k) or {}).get("open") or {}).get("odds"))
            mk = []
            h, d, a = cur("home"), cur("draw"), cur("away")
            if h and d and a:
                mk.append(dict(key="h2h", outcomes=[
                    dict(name=side["home"], price=h), dict(name="Draw", price=d),
                    dict(name=side["away"], price=a)]))
            tot = od.get("total") or {}
            def line(k):
                x = (tot.get(k) or {})
                x = x.get("close") or x.get("open") or {}
                return x.get("line"), _american(x.get("odds"))
            (lo, po), (lu, pu) = line("over"), line("under")
            try:
                pt = float(str(lo)[1:]) if lo else od.get("overUnder")
            except ValueError:
                pt = od.get("overUnder")
            if po and pu and pt is not None and (not lu or float(str(lu)[1:]) == pt):
                mk.append(dict(key="totals", outcomes=[
                    dict(name="Over", point=float(pt), price=po),
                    dict(name="Under", point=float(pt), price=pu)]))
            if mk:
                bks.append(dict(key=(od.get("provider") or {}).get("name", "espn"),
                                markets=mk))
        out.append(dict(home_team=side["home"], away_team=side["away"],
                        commence_time=e["date"], bookmakers=bks))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="../data")
    args = ap.parse_args()
    key = os.environ.get("ODDS_API_KEY", "").strip()
    path = f"{args.data_dir}/match_odds.json"
    try:
        old = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    fixtures = json.load(open(f"{args.data_dir}/fixtures_2627.json",
                              encoding="utf-8"))
    started = {str(f["id"]) for f in fixtures if f.get("started")}
    events, source = None, None
    if key:
        try:
            events = fetch_live(key, regions="uk", markets="h2h,totals")
            source = "the-odds-api.com, UK bookmakers averaged"
        except Exception as ex:                      # quota, outage: fall back
            print(f"match odds: the-odds-api failed ({ex}), trying ESPN")
    if events is None:
        events = fetch_espn()
        source = "DraftKings via ESPN"
    pairs, missed = match_events(events, fixtures, team_lookup(args.data_dir))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # keep everything already held; a started fixture keeps its last
    # pre-match line, and the file only ever holds this season's ids
    keep = dict(old.get("fixtures") or {})
    n = 0
    for f, ev in pairs:
        fid = str(f["id"])
        if fid in started:
            continue
        p = price(ev)
        if p is None:
            continue
        po, ln = over_under(ev)
        keep[fid] = dict(h=f["team_h"], a=f["team_a"], ko=f["kickoff_time"],
                         **p, po=po, ln=ln,
                         **fit_scores(p["ph"], p["pd"], p["pa"], po, ln or 2.5),
                         src=source, pulled=now)
        n += 1
    out = dict(generated=now, source=source,
               fixtures=dict(sorted(keep.items(), key=lambda kv: int(kv[0]))))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print(f"match odds: {n} fixtures priced from {len(events)} events"
          + (f"; unmatched: {', '.join(missed)}" if missed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

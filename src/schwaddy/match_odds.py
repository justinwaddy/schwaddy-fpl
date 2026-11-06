"""Match-winner odds for the Live tab.

Once a day (the 09:35 UK morning refresh) this pulls the Premier League
1X2 market from the-odds-api.com, averages every UK bookmaker's price,
strips the margin out, and writes data/match_odds.json keyed by FPL
fixture id:

    {"generated": "...Z", "source": "the-odds-api.com",
     "fixtures": {"123": {"h": 1, "a": 6, "ko": "...Z",
                          "ph": 0.52, "pd": 0.25, "pa": 0.23,
                          "oh": 1.85, "od": 3.6, "oa": 4.0,
                          "books": 14, "pulled": "...Z"}}}

ph/pd/pa are the de-vigged chances of a home win, draw and away win;
oh/od/oa the average decimal prices they came from. A fixture keeps the
last line pulled before it kicked off: once it has started the bookies
only post in-play prices, which are not what the tab is showing, so a
started fixture is never overwritten.

Needs ODDS_API_KEY in the environment (a GitHub Actions secret). Free
tier is 500 requests a month; one pull of one market in one region costs
one, so a pull a day plus the odd code push is nowhere near it. With no
key it says so and leaves the file alone.

    python -m schwaddy.match_odds --data-dir ../data
"""
import argparse, csv, json, os, re, sys
from datetime import datetime, timezone

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="../data")
    args = ap.parse_args()
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key:
        print("match odds: no ODDS_API_KEY set, leaving match_odds.json as it is")
        return 0
    path = f"{args.data_dir}/match_odds.json"
    try:
        old = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    fixtures = json.load(open(f"{args.data_dir}/fixtures_2627.json",
                              encoding="utf-8"))
    started = {str(f["id"]) for f in fixtures if f.get("started")}
    events = fetch_live(key, regions="uk", markets="h2h")
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
        keep[fid] = dict(h=f["team_h"], a=f["team_a"], ko=f["kickoff_time"],
                         **p, pulled=now)
        n += 1
    out = dict(generated=now, source="the-odds-api.com, UK bookmakers averaged",
               fixtures=dict(sorted(keep.items(), key=lambda kv: int(kv[0]))))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print(f"match odds: {n} fixtures priced from {len(events)} events"
          + (f"; unmatched: {', '.join(missed)}" if missed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

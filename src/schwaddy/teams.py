"""Team strength columns for the live season's teams file.

FPL's bootstrap-static used to publish six strength ratings a club
(overall, attack and defence, home and away, on a 1000-1400 scale) and a
1-5 `strength`. For 2026/27 it publishes `strength` as null and every
attack and defence rating as zero, and the archive's teams.csv - which
refresh.pull re-downloads every run - faithfully carries the zeros. No
model in this repo reads those columns (evo/features.py measures club
strength from goals scored and conceded, and panel.py reads the file
for id-to-name only), but a file of zeros is a trap for anything that
does, so this fills them from the last season that had them:

  carried_<season>   a club that was in the league last season keeps
                     its ratings from then. They move slowly year to
                     year, and last year's number is a far better prior
                     than nought.
  promoted_prior     a club that was not gets the WEAKEST rating in each
                     column from that season - the tail of the
                     distribution, which is the same prior features.py
                     gives a promoted club's goal rates.
  api                the API published a real value; nothing is touched.

A `strength_source` column records which, so that the file says where
its numbers came from rather than passing derived ones off as published.
Nothing is filled where the API publishes real ratings, so when FPL
brings them back the file follows automatically.
"""
import csv
import os

from .panel import SEASONS, LIVE

RATINGS = ["strength_overall_home", "strength_overall_away",
           "strength_attack_home", "strength_attack_away",
           "strength_defence_home", "strength_defence_away"]
SOURCE = "strength_source"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _blank(r):
    """The API's placeholder shape: no attack or defence rating at all."""
    return all(_num(r.get(c)) == 0 for c in RATINGS[2:])


def fill_strengths(data_dir, season=LIVE, prev=None, verbose=True):
    """Fill the live teams file's strength columns where the API left them
    blank. Returns the number of clubs filled (0 when nothing needed it,
    or when either file is missing)."""
    prev = prev or SEASONS[-1]
    path = os.path.join(data_dir, f"teams_{season}.csv")
    ppath = os.path.join(data_dir, f"teams_{prev}.csv")
    if not (os.path.exists(path) and os.path.exists(ppath)):
        return 0
    with open(path, newline="") as fh:
        rd = csv.DictReader(fh)
        fields = list(rd.fieldnames or [])
        rows = list(rd)
    with open(ppath, newline="") as fh:
        prows = {r["code"]: r for r in csv.DictReader(fh) if r.get("code")}
    if not rows or not prows or not any(_blank(r) for r in rows):
        return 0
    # the weakest of last season's clubs, column by column
    prior = {c: min(_num(r.get(c)) for r in prows.values()) for c in RATINGS}
    prior["strength"] = min(_num(r.get("strength")) for r in prows.values())
    filled = 0
    for r in rows:
        if not _blank(r):
            r[SOURCE] = "api"
            continue
        p = prows.get(r.get("code"))
        if p and not _blank(p):
            for c in RATINGS:
                r[c] = p.get(c, "")
            r["strength"] = p.get("strength", "")
            r[SOURCE] = f"carried_{prev}"
        else:
            for c in RATINGS:
                r[c] = f"{prior[c]:g}"
            r["strength"] = f"{prior['strength']:g}"
            r[SOURCE] = "promoted_prior"
        filled += 1
    if SOURCE not in fields:
        fields.append(SOURCE)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    if verbose:
        kinds = {}
        for r in rows:
            kinds[r[SOURCE]] = kinds.get(r[SOURCE], 0) + 1
        print(f"team strengths: {filled} clubs filled in {path} ("
              + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())) + ")")
    return filled


if __name__ == "__main__":
    import sys
    fill_strengths(sys.argv[1] if len(sys.argv) > 1 else "data")

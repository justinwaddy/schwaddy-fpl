"""Draft board v0: project 2026/27 draft-scoring points and compute VORP.

Method (v0 heuristic, to be replaced by matrix completion in later sessions):
1. Recompute each 2025/26 player-gameweek under DRAFT scoring (no captain,
   bonus at 1x BPS-awarded bonus, DefCon thresholds from settings).
2. Per-player expected points per appearance, xG/xA-adjusted: blend actual
   goal involvements with expected (0.5/0.5) to strip finishing noise.
3. Shrink to positional mean with weight n/(n+K), K=10 appearances.
4. Season projection = rate * expected appearances (from minutes share and
   current status flags in the 26/27 bootstrap).
5. Newcomers without 25/26 PL data: impute from FPL draft_rank percentile
   within position.
6. VORP: replacement level = projection of the (n_drafted_at_pos + 1)-th
   ranked player for a 6-team league (2/5/5/3 squads).
"""
import csv, json, math
from collections import defaultdict

LEAGUE_SIZE = 6
SQUAD = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
APPEARANCES = 38.0

d = json.load(open("draft_bootstrap.json"))
c = json.load(open("classic_bootstrap.json"))
S = d["settings"]["scoring"]
POS = {t["id"]: t["singular_name_short"] for t in d["element_types"]}
TEAMS = {t["id"]: t["short_name"] for t in d["teams"]}

# id -> code map for 25/26
code_of_2526 = {}
for row in csv.DictReader(open("players_raw_2025-26.csv")):
    code_of_2526[row["id"]] = row["code"]

def draft_points(r, pos):
    m = int(r["minutes"] or 0)
    if m == 0:
        return 0.0, 0
    p = S["long_play"] if m >= S["long_play_limit"] else S["short_play"]
    g = int(r["goals_scored"] or 0)
    p += g * S[f"goals_scored_{pos}"]
    # xG blend: half credit actual, half expected, at position goal value
    xg = float(r["expected_goals"] or 0)
    p += 0.5 * (xg - g) * S[f"goals_scored_{pos}"]
    a = int(r["assists"] or 0)
    xa = float(r["expected_assists"] or 0)
    p += a * S["assists"] + 0.5 * (xa - a) * S["assists"]
    if m >= 60 and int(r["clean_sheets"] or 0):
        p += S[f"clean_sheets_{pos}"]
    gc = int(r["goals_conceded"] or 0)
    if pos in ("GKP", "DEF"):
        p += (gc // S["concede_limit"]) * S[f"goals_conceded_{pos}"]
    sv = int(r["saves"] or 0)
    p += (sv // S["saves_limit"]) * S["saves"]
    dc = int(float(r["defensive_contribution"] or 0))
    lim = S[f"defensive_contribution_limit_{pos}"]
    if lim and dc >= lim:
        p += S[f"defensive_contribution_{pos}"]
    p += int(r["bonus"] or 0) * S["bonus"]
    p += int(r["yellow_cards"] or 0) * S["yellow_cards"]
    p += int(r["red_cards"] or 0) * S["red_cards"]
    p += int(r["own_goals"] or 0) * S["own_goals"]
    p += int(r["penalties_saved"] or 0) * S["penalties_saved"]
    p += int(r["penalties_missed"] or 0) * S["penalties_missed"]
    return p, 1 if m >= 45 else 0

# aggregate 25/26 by player code
agg = defaultdict(lambda: {"pts": 0.0, "apps": 0, "mins": 0, "gws": 0})
for r in csv.DictReader(open("gws_2025-26.csv")):
    code = code_of_2526.get(r["element"])
    if not code:
        continue
    pos = r["position"].replace("GK", "GKP") if r["position"] == "GK" else r["position"]
    if pos not in SQUAD:
        continue
    pts, _ = draft_points(r, pos)
    a = agg[code]
    m = int(r["minutes"] or 0)
    a["pts"] += pts
    a["mins"] += m
    a["gws"] += 1
    if m > 0:
        a["apps"] += 1

# positional per-appearance means for shrinkage (weighted by apps)
pos_rate = defaultdict(list)
cur = {str(e["code"]): e for e in d["elements"]}
for code, a in agg.items():
    e = cur.get(code)
    if not e or a["apps"] < 4:
        continue
    pos_rate[POS[e["element_type"]]].append(a["pts"] / a["apps"])
pos_mean = {k: sum(v) / len(v) for k, v in pos_rate.items()}

K = 10.0
players = []
for e in d["elements"]:
    pos = POS[e["element_type"]]
    code = str(e["code"])
    a = agg.get(code)
    dr = e.get("draft_rank") or 9999
    status = e["status"]
    news = e.get("news") or ""
    cop = e.get("chance_of_playing_next_round")
    if a and a["apps"] >= 2:
        rate_raw = a["pts"] / a["apps"]
        w = a["apps"] / (a["apps"] + K)
        rate = w * rate_raw + (1 - w) * pos_mean[pos]
        mins_share = min(1.0, a["mins"] / (a["gws"] * 90.0)) if a["gws"] else 0.5
        exp_apps = APPEARANCES * (0.35 + 0.65 * mins_share)
        src = "25/26"
    else:
        rate = None
        exp_apps = None
        src = "rank-imputed"
    players.append(dict(code=code, name=e["web_name"],
                        full=f'{e["first_name"]} {e["second_name"]}',
                        pos=pos, team=TEAMS[e["team"]], draft_rank=dr,
                        status=status, news=news, cop=cop,
                        rate=rate, exp_apps=exp_apps, src=src))

# impute newcomers from draft_rank percentile within position
by_pos_known = defaultdict(list)
for p in players:
    if p["rate"] is not None:
        by_pos_known[p["pos"]].append(p)
for k in by_pos_known:
    by_pos_known[k].sort(key=lambda p: -(p["rate"] * p["exp_apps"]))
by_pos_rank = defaultdict(list)
for p in players:
    by_pos_rank[p["pos"]].append(p)
for k in by_pos_rank:
    by_pos_rank[k].sort(key=lambda p: p["draft_rank"])
for k, lst in by_pos_rank.items():
    known = by_pos_known[k]
    n = len(lst)
    for i, p in enumerate(lst):
        if p["rate"] is None:
            q = i / max(1, n - 1)
            j = min(len(known) - 1, int(q * len(known)))
            ref = known[j]
            p["rate"] = 0.85 * ref["rate"]  # newcomer discount
            p["exp_apps"] = 0.9 * ref["exp_apps"]

for p in players:
    avail = 1.0
    if p["status"] in ("i", "s"):
        avail = 0.8 if (p["cop"] or 0) else 0.65
    elif p["status"] == "d":
        avail = 0.9
    elif p["status"] == "u":
        avail = 0.05
    p["proj"] = round(p["rate"] * p["exp_apps"] * avail, 1)

# Blend with FPL draft_rank prior: anchor to their ordering on my point scale.
# Weight on my own projection rises with 25/26 appearances, so players with
# injury-truncated seasons (Isak, Palmer) get pulled toward their rank prior.
mine_sorted = sorted((p["proj"] for p in players), reverse=True)
for p in players:
    dr = p["draft_rank"]
    if dr and dr <= 300:
        implied = mine_sorted[min(dr - 1, len(mine_sorted) - 1)]
        apps = agg.get(p["code"], {}).get("apps", 0)
        w = min(1.0, apps / 30.0) if p["src"] == "25/26" else 0.3
        p["proj"] = round(w * p["proj"] + (1 - w) * implied, 1)

# VORP: replacement = (drafted+1)-th at position
by_pos = defaultdict(list)
for p in players:
    by_pos[p["pos"]].append(p)
repl = {}
for k, lst in by_pos.items():
    lst.sort(key=lambda p: -p["proj"])
    n_draft = LEAGUE_SIZE * SQUAD[k]
    repl[k] = lst[n_draft]["proj"] if len(lst) > n_draft else 0.0
for p in players:
    p["vorp"] = round(p["proj"] - repl[p["pos"]], 1)

players.sort(key=lambda p: -p["vorp"])
out = [p for p in players if p["status"] != "u"][:250]
json.dump(dict(generated="2026-08-20", league_size=LEAGUE_SIZE,
               replacement=repl, players=out),
          open("board.json", "w"), indent=1)
print("replacement levels:", repl)
for p in out[:25]:
    print(f'{p["vorp"]:6.1f} {p["proj"]:6.1f}  {p["pos"]:4} {p["name"]:20} {p["team"]:4} {p["src"]:12} {p["news"][:30]}')

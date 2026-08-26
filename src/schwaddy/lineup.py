"""Weekly XI, bench order, and waiver recommendations for FPL Draft.

Formation rules from the draft API settings: 11 starters, exactly 1 GKP,
3-5 DEF, 2-5 MID, 1-3 FWD, no captains. XI selection enumerates the valid
(def, mid, fwd) counts and takes the top projected players at each
position, which is exact for this constraint set.

Availability: P(plays GW) from recent minutes share and the API status
flags; expected GW points = P(plays) * projected per-match points.

Waivers need the numeric league id: element-status marks who owns whom.
A claim is recommended when a free agent's remaining-season expected
points exceed the weakest same-position squad member's by more than the
switching margin.
"""
import numpy as np

FORMATIONS = [(d, m, 10 - d - m) for d in range(3, 6) for m in range(2, 6)
              if 1 <= 10 - d - m <= 3]


def p_plays(D_row_window, status, chance, share=None):
    """P(plays the next gameweek), before any injury flag is applied.

    share: minutes played over minutes available across the trailing
    window (see liveform.trailing_share). Preferred when available.
    D_row_window is a bare appearance indicator, so it scores a 15-minute
    cameo the same as a full start and overrates anyone who has lost their
    place; it stays as the fallback for players with no minutes history.
    """
    n = max(1, len(D_row_window))
    played = float(D_row_window.sum())
    if share is not None:
        p = 0.35 + 0.65 * min(1.0, share)
    elif played > 0:
        p = 0.35 + 0.65 * min(1.0, played / n)
    else:
        p = 0.40
    if status in ("i", "s"):
        p *= (chance / 100.0) if chance not in (None, 0) else 0.65
    elif status == "d":
        p *= (chance / 100.0) if chance not in (None, 0) else 0.90
    elif status == "u":
        p = 0.02
    return p


def expected_gw_points(pred_col, avail):
    return pred_col * avail


def pick_xi(squad):
    """squad: list of dicts with keys pos, ep (expected points this GW).
    Returns (starters, bench_order, formation)."""
    by = {k: sorted([p for p in squad if p["pos"] == k],
                    key=lambda p: -p["ep"]) for k in ("GKP", "DEF", "MID", "FWD")}
    best = (-np.inf, None, None)
    for d, m, f in FORMATIONS:
        if len(by["GKP"]) < 1 or len(by["DEF"]) < d \
           or len(by["MID"]) < m or len(by["FWD"]) < f:
            continue
        xi = by["GKP"][:1] + by["DEF"][:d] + by["MID"][:m] + by["FWD"][:f]
        tot = sum(p["ep"] for p in xi)
        if tot > best[0]:
            best = (tot, xi, (d, m, f))
    tot, xi, form = best
    names = {p["name"] for p in xi}
    bench_gk = [p for p in by["GKP"][1:]]
    bench_out = sorted([p for p in squad
                        if p["name"] not in names and p["pos"] != "GKP"],
                       key=lambda p: -p["ep"])
    return xi, bench_gk + bench_out, form


def waiver_claims(squad, free_agents, margin=8.0, top=5):
    """Both lists: dicts with pos, name, rest (remaining-season expected
    points). Recommends drop/add pairs ranked by gain."""
    out = []
    for pos in ("GKP", "DEF", "MID", "FWD"):
        mine = sorted([p for p in squad if p["pos"] == pos],
                      key=lambda p: p["rest"])
        if not mine:
            continue
        weakest = mine[0]
        for fa in sorted([p for p in free_agents if p["pos"] == pos],
                         key=lambda p: -p["rest"])[:top]:
            gain = fa["rest"] - weakest["rest"]
            if gain > margin:
                out.append(dict(add=fa["name"], drop=weakest["name"],
                                pos=pos, gain=round(gain, 1)))
    return sorted(out, key=lambda c: -c["gain"])

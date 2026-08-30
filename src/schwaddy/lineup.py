"""Weekly XI, bench order, and waiver recommendations for FPL Draft.

Formation rules from the draft API settings: 11 starters, exactly 1 GKP,
3-5 DEF, 2-5 MID, 1-3 FWD, no captains. XI selection enumerates the valid
(def, mid, fwd) counts and takes the top projected players at each
position, which is exact for this constraint set.

Availability: P(plays GW) from recent minutes share and the API status
flags; expected GW points = P(plays) * projected per-match points.

Waivers need the numeric league id: element-status marks who owns whom.
A claim is recommended when a free agent's FIVE-GAMEWEEK expected points
exceed the weakest same-position squad member's by more than the
switching margin. Rest-of-season totals were used until it was noticed
that they are almost fixture-free - every club plays every other one over
38 gameweeks - so they cannot express a good or bad run.
"""
import numpy as np

FORMATIONS = [(d, m, 10 - d - m) for d in range(3, 6) for m in range(2, 6)
              if 1 <= 10 - d - m <= 3]

# Floor on P(plays) for a player with no minutes behind him. It was 0.35,
# which badly overstated players who were not being picked: measured
# rolling-origin, that band appeared about 9% of the time. Sweeping the
# floor over 23/24, 24/25 and 25/26 cuts the Brier score by 36% at 0.15 in
# every one of the three. Realized XI points are unmoved (+0.95, -1.45,
# +0.24 across those seasons - noise), because an XI is nailed starters
# for whom the floor never binds. This is a fix to the number's honesty,
# which is what waivers and the dashboard read, not to team selection.
PLAY_FLOOR = 0.15
UNKNOWN_PLAYER = 0.40      # no history either way: neither seen nor dropped


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
        p = PLAY_FLOOR + (1.0 - PLAY_FLOOR) * min(1.0, share)
    elif played > 0:
        p = PLAY_FLOOR + (1.0 - PLAY_FLOOR) * min(1.0, played / n)
    else:
        p = UNKNOWN_PLAYER
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


def waiver_claims(squad, free_agents, margin=2.0, top=5, key="next5"):
    """Both lists: dicts with pos, name, and the ranking field.

    key defaults to "next5", the fixture-adjusted five-gameweek total,
    not "rest". Over a whole season every club plays every other one, so
    rest-of-season totals are very nearly fixture-free and a claim made on
    them ignores the run the player is actually about to face. margin is
    on the same scale as key, so it drops with the horizon.
    """
    out = []
    for pos in ("GKP", "DEF", "MID", "FWD"):
        mine = sorted([p for p in squad if p["pos"] == pos],
                      key=lambda p: p.get(key, p["rest"]))
        if not mine:
            continue
        weakest = mine[0]
        for fa in sorted([p for p in free_agents if p["pos"] == pos],
                         key=lambda p: -p.get(key, p["rest"]))[:top]:
            gain = fa.get(key, fa["rest"]) - weakest.get(key, weakest["rest"])
            if gain > margin:
                out.append(dict(add=fa["name"], drop=weakest["name"],
                                pos=pos, gain=round(gain, 1)))
    return sorted(out, key=lambda c: -c["gain"])

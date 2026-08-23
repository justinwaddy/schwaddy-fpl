"""Rank-aware endgame layer: maximize P(win the league), not expected points.

With aggregate scoring, late-season decisions should depend on the table.
Trailing with few gameweeks left, the P(win)-maximizing XI accepts lower
expected points for higher variance (ceiling differentials); leading, the
reverse. This layer searches along the mean-variance frontier: candidate
XIs are pick_xi under tilted values ep + kappa * sd for a grid of kappa,
each candidate's P(win) is estimated by Monte Carlo over the remaining
gameweeks, and the best candidate is returned.

Player sd per match is the residual standard deviation around the model's
predictions over the player's observed cells, shrunk toward the position
mean (k=10). Draws are independent across players and gameweeks, which
understates same-team covariance; treat P(win) as a ranking of decisions,
not a calibrated probability.

Rivals are assumed to field their EV-greedy XI each week (kappa = 0) and
to keep their current squads (no future waivers), so the comparison
isolates today's decision.
"""
import numpy as np

from .lineup import pick_xi

KAPPA_GRID = (-0.6, -0.3, 0.0, 0.3, 0.6, 1.0)


def player_sd(Y, D, pred, pos_idx, k=10.0):
    """Per-match residual sd per player, shrunk to position mean."""
    N = Y.shape[0]
    sd = np.zeros(N)
    n = np.zeros(N)
    for i in range(N):
        m = D[i] > 0
        n[i] = m.sum()
        if n[i] >= 2:
            sd[i] = float((Y[i, m] - pred[i, m]).std(ddof=1))
    pos_mean = {}
    for p in set(pos_idx.values()):
        sel = [i for i in range(N) if pos_idx[i] == p and n[i] >= 8]
        pos_mean[p] = np.mean([sd[i] for i in sel]) if sel else 2.5
    return np.array([(n[i] * sd[i] + k * pos_mean[pos_idx[i]]) / (n[i] + k)
                     for i in range(N)])


def _xi_for(squad, kappa):
    sq = [dict(name=p["name"], pos=p["pos"], ep=p["ep"] + kappa * p["sd"])
          for p in squad]
    xi, _, _ = pick_xi(sq)
    names = {q["name"] for q in xi}
    return [p for p in squad if p["name"] in names]


def p_win(my_xi_by_gw, rival_xis_by_gw, my_total, rival_totals, sims=4000,
          seed=3):
    """Monte Carlo P(strictly first) given fixed XIs for remaining GWs.
    Each entry of *_by_gw is a list of (ep, sd) tuples per starter."""
    rng = np.random.default_rng(seed)
    def season_draws(xis):
        mu = sum(ep for gw in xis for ep, _ in gw)
        var = sum(s * s for gw in xis for _, s in gw)
        return rng.normal(mu, np.sqrt(max(var, 1e-9)), size=sims)
    mine = my_total + season_draws(my_xi_by_gw)
    rivals = [rival_totals[j] + season_draws(rival_xis_by_gw[j])
              for j in range(len(rival_xis_by_gw))]
    top_rival = np.max(np.vstack(rivals), axis=0)
    return float((mine > top_rival).mean())


def choose_endgame_xi(my_squad_by_gw, rival_squads_by_gw, my_total,
                      rival_totals, kappa_grid=KAPPA_GRID, sims=4000):
    """my_squad_by_gw: list over remaining GWs of squads (dicts with name,
    pos, ep, sd). Rivals: list of managers, each a list over GWs. The
    kappa tilt is applied to ALL remaining gameweeks of my XI choice;
    rivals play kappa = 0. Returns (best_kappa, best_p, this_gw_xi,
    table of (kappa, p_win))."""
    rival_xis = [[[(p["ep"], p["sd"]) for p in _xi_for(sq, 0.0)]
                  for sq in mgr] for mgr in rival_squads_by_gw]
    results = []
    for kap in kappa_grid:
        mine = [[(p["ep"], p["sd"]) for p in _xi_for(sq, kap)]
                for sq in my_squad_by_gw]
        pw = p_win(mine, rival_xis, my_total, rival_totals, sims=sims)
        results.append((kap, pw))
    best_kap, best_p = max(results, key=lambda r: r[1])
    return best_kap, best_p, _xi_for(my_squad_by_gw[0], best_kap), results

"""TROP-forecast: masked weighted TWFE + nuclear-norm low-rank, no tau.

Python port of the estimation core of Justin's Stata `trop` package
(justinwaddy/TROP, v0.2.8, Clarke & Waddy), rearranged for forecasting:
the treated block becomes the forecast block (all units, future columns),
tau is dropped, and free time effects are replaced by season plus
gameweek-of-season effects so that future columns are identified from the
same gameweek numbers in past seasons.

Objective (D_it = 1 observed, delta = D * (omega * theta)):

  min over mu, a_i, g_s, h_gw, beta, L of
    sum_i sum_t delta_it * (Y_it - mu - a_i - g_season(t) - h_gw(t)
                            - X_it' beta - L_it)^2  + lambda_nn * ||L||_*

Conventions mirrored from trop.ado v0.2.8:
- outcome standardized ((Y - mean)/sd) before fitting, mapped back after
- theta_t = exp(-lambda_time * d_t), here d_t = distance from column t to
  the last observed column (recency weighting toward the forecast block)
- omega_i = 1 (no donor role in forecasting; documented design choice)
- profiled FISTA: inner WLS solved via a Ginv built ONCE per delta
- step = 1/(2*max(delta)); SVT threshold = step*lambda_nn
- adaptive restart when sum((Yk - L_new) * (L_new - L)) > 0
- stopping on relative objective change (and relative change in the
  forecast-block prediction, the analog of dtau)
- descending lambda_nn path with warm starts; lambda_nn = inf -> WLS
- CV: hold out the last cv_holdout observed gameweek columns as a
  placebo forecast block, joint (lambda_time x lambda_nn path) search
"""
import numpy as np
from scipy import sparse


def _design(N, T, season_of, gw_of, X, groups=None):
    """Sparse design for (mu, a_i, g_s, h_gw, [group dummies], beta):
    reference categories dropped. groups (e.g. positions) enter
    unpenalized so a ridge on unit effects shrinks players toward their
    group mean rather than the reference unit."""
    S = int(season_of.max()) + 1
    G = int(gw_of.max()) + 1
    q = X.shape[2] if X is not None else 0
    ng = (int(groups.max()) if groups is not None else 0)
    p = 1 + (N - 1) + (S - 1) + (G - 1) + ng + q
    rows, cols, vals = [], [], []
    idx = np.arange(N * T)
    ii = idx // T
    tt = idx % T
    rows.append(idx); cols.append(np.zeros(N * T, int)); vals.append(np.ones(N * T))
    m = ii >= 1
    rows.append(idx[m]); cols.append(ii[m]); vals.append(np.ones(m.sum()))
    off = N
    sarr = season_of[tt]
    m = sarr >= 1
    rows.append(idx[m]); cols.append(off + sarr[m] - 1); vals.append(np.ones(m.sum()))
    off += S - 1
    garr = gw_of[tt]
    m = garr >= 1
    rows.append(idx[m]); cols.append(off + garr[m] - 1); vals.append(np.ones(m.sum()))
    off += G - 1
    if groups is not None:
        garr2 = groups[ii]
        for g2 in range(1, ng + 1):
            m = garr2 == g2
            rows.append(idx[m]); cols.append(np.full(m.sum(), off + g2 - 1))
            vals.append(np.ones(m.sum()))
        off += ng
    for j in range(q):
        xv = X[:, :, j].reshape(-1)
        m = xv != 0
        rows.append(idx[m]); cols.append(np.full(m.sum(), off + j)); vals.append(xv[m])
    Z = sparse.csr_matrix((np.concatenate(vals),
                           (np.concatenate(rows), np.concatenate(cols))),
                          shape=(N * T, p))
    return Z


class TropForecast:
    def __init__(self, lambda_time=0.05, lambda_nn=1.0,
                 tol=1e-6, max_iter=5000, ridge_unit=0.0, unit_groups=None):
        self.lambda_time = lambda_time
        self.lambda_nn = lambda_nn
        self.tol = tol
        self.max_iter = max_iter
        self.ridge_unit = ridge_unit          # shrink a_i toward group mean
        self.unit_groups = unit_groups        # int array len N (positions)

    # ---- weights (trop_time_weights2 analog, forecast distances) ----
    def _theta(self, T, obs_cols, lam):
        t_last = obs_cols.max()
        d = np.maximum(0.0, t_last - np.arange(T))
        return np.exp(-lam * d)

    def _prepare(self, Y, D, season_of, gw_of, X, lam_t):
        N, T = Y.shape
        obs_cols = np.where(D.any(axis=0))[0]
        theta = self._theta(T, obs_cols, lam_t)
        delta = D * theta[None, :]                       # omega_i = 1
        Z = _design(N, T, season_of, gw_of, X, groups=self.unit_groups)
        Zw = sparse.diags(delta.reshape(-1)) @ Z
        Gram = (Z.T @ Zw).toarray()
        if self.ridge_unit > 0:
            for j in range(1, N):                # unit-dummy columns only
                Gram[j, j] += self.ridge_unit
        Ginv = np.linalg.pinv(Gram)                      # once per delta
        return delta, Z, Zw, Ginv

    def _wls_fit(self, Ytil, Z, Zw, Ginv, N, T):
        coef = Ginv @ (Zw.T @ Ytil.reshape(-1))
        fit = np.asarray(Z @ coef).reshape(N, T)
        return coef, fit

    @staticmethod
    def _svt(Zm, thr):
        U, s, Vt = np.linalg.svd(Zm, full_matrices=False)
        s_thr = np.maximum(s - thr, 0.0)
        return (U * s_thr) @ Vt, s_thr.sum()

    def _core(self, Y, D, delta, Z, Zw, Ginv, lam_nn, L, cv_mode,
              pred_mask=None):
        """trop_nuclear_core analog. Returns (L, coef, iters)."""
        N, T = Y.shape
        maxd = delta.max()
        step = 1.0 / (2.0 * maxd)
        thr = step * lam_nn
        L_prev = L.copy()
        a = 1.0
        obj_old = np.inf
        pred_old = None
        Ymasked = np.where(D > 0, Y, 0.0)
        for k in range(1, self.max_iter + 1):
            a_next = (1.0 + np.sqrt(1.0 + 4.0 * a * a)) / 2.0
            mom = (a - 1.0) / a_next
            Yk = L + mom * (L - L_prev)
            Ytil = Ymasked - Yk
            coef, fit = self._wls_fit(Ytil, Z, Zw, Ginv, N, T)
            R = Ytil - fit
            Gstep = Yk + (delta * R) / maxd
            L_new, nucnorm = self._svt(Gstep, thr)
            if np.sum((Yk - L_new) * (L_new - L)) > 0:   # adaptive restart
                a_next = 1.0
            coef, fit = self._wls_fit(Ymasked - L_new, Z, Zw, Ginv, N, T)
            R = (Ymasked - L_new) - fit
            obj_new = np.sum(delta * R * R) + lam_nn * nucnorm
            dobj = (abs(obj_new - obj_old) / (1.0 + abs(obj_old))
                    if np.isfinite(obj_old) else np.inf)
            done = dobj < self.tol
            if cv_mode and pred_mask is not None:        # dtau analog
                pred = (fit + L_new)[pred_mask]
                if pred_old is not None:
                    dpred = np.abs(pred - pred_old).max() / (1.0 + np.abs(pred_old).max())
                    done = dpred < self.tol
                pred_old = pred
            L_prev, L, a, obj_old = L, L_new, a_next, obj_new
            if k > 1 and done:
                break
        coef, _ = self._wls_fit(Ymasked - L, Z, Zw, Ginv, N, T)
        return L, coef, k

    def _path(self, Y, D, delta, Z, Zw, Ginv, nn_grid, cv_mode, pred_mask):
        """Descending warm-started lambda_nn path (trop_nuclear_path_suff)."""
        N, T = Y.shape
        out = {}
        finite = sorted([l for l in nn_grid if np.isfinite(l)], reverse=True)
        L = np.zeros((N, T))
        for lam in finite:
            L, coef, _ = self._core(Y, D, delta, Z, Zw, Ginv, lam, L,
                                    cv_mode, pred_mask)
            out[lam] = (L.copy(), coef.copy())
        if any(not np.isfinite(l) for l in nn_grid):     # inf -> plain WLS
            coef, _ = self._wls_fit(np.where(D > 0, Y, 0.0), Z, Zw, Ginv, N, T)
            out[np.inf] = (np.zeros((N, T)), coef)
        return out

    def cv(self, Y, D, season_of, gw_of, X=None,
           time_grid=(0.0, 0.02, 0.05, 0.1, 0.2),
           nn_grid=(0.25, 0.5, 1.0, 2.0, 4.0, np.inf),
           cv_holdout=6, n_blocks=3, tol=None, verbose=True):
        """Placebo forecast CV over n_blocks terminal cutoffs: for block b,
        pretend the panel ends cv_holdout*b columns earlier, mask everything
        from there on (true forecasting observes nothing after the cutoff),
        and score RMSE on the first cv_holdout masked observed cells.
        Average RMSE across blocks; joint (time x nn path) search."""
        tol0 = self.tol
        self.tol = tol if tol is not None else 1e-4      # cv_mode tolerance
        obs_cols = np.where(D.any(axis=0))[0]
        scores = {}
        for b in range(n_blocks):
            cut = len(obs_cols) - cv_holdout * (b + 1)
            hold = obs_cols[cut:cut + cv_holdout]
            Dtr = D.copy()
            Dtr[:, obs_cols[cut]:] = 0                   # terminal mask
            score_mask = np.zeros_like(D, dtype=bool)
            score_mask[:, hold] = D[:, hold] > 0
            mu, sd = self._std_params(Y, Dtr)
            Ystd = (Y - mu) / sd
            for lam_t in time_grid:
                delta, Z, Zw, Ginv = self._prepare(Ystd, Dtr, season_of,
                                                   gw_of, X, lam_t)
                fits = self._path(Ystd, Dtr, delta, Z, Zw, Ginv, nn_grid,
                                  cv_mode=True, pred_mask=score_mask)
                for lam_nn, (L, coef) in fits.items():
                    N, T = Y.shape
                    Lx = self._extend_factors(L, Dtr)
                    pred = np.asarray(Z @ coef).reshape(N, T) + Lx
                    err = (Ystd - pred)[score_mask]
                    rmse = float(np.sqrt(np.mean(err * err)))
                    scores.setdefault((lam_t, lam_nn), []).append(rmse)
        best = (np.inf, None, None)
        for (lam_t, lam_nn), v in sorted(scores.items()):
            avg = float(np.mean(v))
            if verbose:
                print(f"  cv lambda_time={lam_t:<5} lambda_nn={lam_nn:<6} "
                      f"rmse={avg:.4f}  (blocks: "
                      + " ".join(f"{r:.3f}" for r in v) + ")")
            if avg < best[0]:
                best = (avg, lam_t, lam_nn)
        self.tol = tol0
        self.lambda_time, self.lambda_nn = best[1], best[2]
        self.cv_rmse_ = best[0]
        return best

    def cv_utility(self, Y, D, season_of, gw_of, pos_idx, X=None,
                   time_grid=(0.0, 0.01, 0.03, 0.06),
                   nn_grid=(5.0, 10.0, np.inf),
                   cv_holdout=6, n_blocks=3, tol=None, verbose=True):
        """Select lambdas under the decision loss actually played: on each
        placebo block, pick a legal XI for every held-out gameweek by
        prediction times trailing P(play) and score its REALIZED points.
        Choose the (lambda_time, lambda_nn) maximizing mean XI points per
        gameweek; RMSE is reported alongside as a regression guard.
        Trailing minutes before each held GW are legitimately known at
        that GW's deadline, so true D is used for P(play) even inside the
        block; the fit itself only sees data before the cutoff."""
        from .lineup import pick_xi

        def _pplay(i, col):
            lo = max(0, col - 8)
            pl = D[i, lo:col].sum()
            return 0.2 + 0.8 * pl / max(1, col - lo)

        tol0 = self.tol
        self.tol = tol if tol is not None else 1e-4
        obs_cols = np.where(D.any(axis=0))[0]
        N, T = Y.shape
        util, rmse_log = {}, {}
        for b in range(n_blocks):
            cut = len(obs_cols) - cv_holdout * (b + 1)
            hold = obs_cols[cut:cut + cv_holdout]
            Dtr = D.copy()
            Dtr[:, obs_cols[cut]:] = 0
            score_mask = np.zeros_like(D, dtype=bool)
            score_mask[:, hold] = D[:, hold] > 0
            mu, sd = self._std_params(Y, Dtr)
            Ystd = (Y - mu) / sd
            for lam_t in time_grid:
                delta, Z, Zw, Ginv = self._prepare(Ystd, Dtr, season_of,
                                                   gw_of, X, lam_t)
                fits = self._path(Ystd, Dtr, delta, Z, Zw, Ginv, nn_grid,
                                  cv_mode=True, pred_mask=score_mask)
                for lam_nn, (L, coef) in fits.items():
                    Lx = self._extend_factors(L, Dtr)
                    pred = (np.asarray(Z @ coef).reshape(N, T) + Lx) * sd + mu
                    pts = 0.0
                    for col in hold:
                        sq = [dict(name=i, pos=pos_idx[i],
                                   ep=pred[i, col] * _pplay(i, col))
                              for i in range(N)]
                        xi, _, _ = pick_xi(sq)
                        pts = float(sum(Y[p["name"], col]
                                        for p in xi
                                        if D[p["name"], col] > 0))
                        util.setdefault((lam_t, lam_nn), []).append(pts)
                    err = ((Ystd * sd + mu) - pred)[score_mask]
                    rmse_log.setdefault((lam_t, lam_nn), []).append(
                        float(np.sqrt(np.mean(err * err))))
        # selection: paired one-SE rule. The utility criterion is noisy
        # (few held gameweeks), so raw argmax suffers winner's curse.
        # Keep every config within one PAIRED standard error of the
        # utility max (paired per-gameweek differences share the held
        # GWs, so their SE is small), then pick min RMSE among them.
        keys = sorted(util)
        U = {k: np.array(util[k]) for k in keys}
        means = {k: float(U[k].mean()) for k in keys}
        kmax = max(keys, key=lambda k: means[k])
        cand = []
        for k in keys:
            dvec = U[kmax] - U[k]
            se = dvec.std(ddof=1) / np.sqrt(len(dvec)) if len(dvec) > 1 else 0.0
            if dvec.mean() <= se:
                cand.append(k)
        pick = min(cand, key=lambda k: float(np.mean(rmse_log[k])))
        if verbose:
            for k in keys:
                tag = " <=1SE" if k in cand else ""
                tag += "  <- pick" if k == pick else ""
                print(f"  cvU lambda_time={k[0]:<5} lambda_nn={k[1]:<6} "
                      f"XI/gw={means[k]:6.2f}  "
                      f"rmse={np.mean(rmse_log[k]):.4f}{tag}")
        self.tol = tol0
        self.lambda_time, self.lambda_nn = pick[0], pick[1]
        self.cv_utility_ = means[pick]
        return (means[pick], pick[0], pick[1])

    @staticmethod
    def _extend_factors(L, D, ar_max_rank=6):
        """Forecast L into all-missing columns: SVD on observed columns,
        AR(1) with intercept per factor score series, iterate forward.
        Returns L with future columns filled."""
        obs = np.where(D.any(axis=0))[0]
        fut = np.where(~D.any(axis=0))[0]
        fut = fut[fut > obs.max()]
        if len(fut) == 0:
            return L
        U, s, Vt = np.linalg.svd(L[:, obs], full_matrices=False)
        r = min(int((s > 1e-8).sum()), ar_max_rank)
        if r == 0:
            return L
        Lout = L.copy()
        G = (s[:r, None] * Vt[:r, :])                   # r x len(obs) scores
        Gf = np.zeros((r, len(fut)))
        for k in range(r):
            g = G[k]
            x = np.column_stack([np.ones(len(g) - 1), g[:-1]])
            c, phi = np.linalg.lstsq(x, g[1:], rcond=None)[0]
            phi = np.clip(phi, -0.99, 0.99)             # stationary forecast
            prev = g[-1]
            for h in range(len(fut)):
                prev = c + phi * prev
                Gf[k, h] = prev
        Lout[:, fut] = U[:, :r] @ Gf
        return Lout

    @staticmethod
    def _std_params(Y, D):
        v = Y[D > 0]
        return float(v.mean()), float(v.std())

    def fit(self, Y, D, season_of, gw_of, X=None):
        mu, sd = self._std_params(Y, D)
        Ystd = (Y - mu) / sd
        delta, Z, Zw, Ginv = self._prepare(Ystd, D, season_of, gw_of, X,
                                           self.lambda_time)
        if np.isfinite(self.lambda_nn):
            L, coef, iters = self._core(Ystd, D, delta, Z, Zw, Ginv,
                                        self.lambda_nn,
                                        np.zeros_like(Y, dtype=float),
                                        cv_mode=False)
        else:
            coef, _ = self._wls_fit(np.where(D > 0, Ystd, 0.0), Z, Zw, Ginv,
                                    *Y.shape)
            L, iters = np.zeros_like(Y, dtype=float), 0
        N, T = Y.shape
        Lx = self._extend_factors(L, D)
        self.pred_ = ((Z @ coef).reshape(N, T) + Lx) * sd + mu
        self.L_, self.coef_, self.iters_ = Lx * sd, coef, iters
        self.y_mu_, self.y_sd_ = mu, sd
        # covariate slopes, on the standardized scale: multiply by y_sd_
        # for points. Sliced out here because the caller cannot know where
        # they sit without rebuilding the design's column layout.
        S = int(season_of.max()) + 1
        G = int(gw_of.max()) + 1
        ng = int(self.unit_groups.max()) if self.unit_groups is not None else 0
        off = 1 + (N - 1) + (S - 1) + (G - 1) + ng
        self.beta_ = coef[off:] if X is not None and X.shape[2] else \
            np.zeros(0)
        return self

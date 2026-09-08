"""Point-in-time feature tensors for the evolutionary draft player.

Everything in this module obeys one rule: a number attached to a decision
taken at time t is computed from match rows whose kick-off is strictly
before t. The clock is a timestamp, not a gameweek index, because a
postponed fixture breaks the gameweek ordering and a gameweek-indexed
window silently reads the future when it does.

For a season we build, per player:

  X        (n_players, 38, F)  features as at that gameweek's decision
  base_*   heuristic values (see BASELINES) on the same clock
  real     (n_players, 38)     realized draft points, the reward
  minutes  (n_players, 38)     realized minutes, for automatic subs
  pool     (n_players, 38)     who is in the league and available

The decision time for gameweek g is the deadline (90 minutes before the
first kick-off of the gameweek) minus cfg.waiver_lead_hours. Waivers and
the line-up share that one information set: it is the true clock for the
waiver, and it is conservative rather than optimistic for the line-up.

Membership versus performance. To simulate a season you have to know who
was on a Premier League roster; a real manager knew that, and the archive
does not store it by date. The line taken here is that MEMBERSHIP is
known and PERFORMANCE never is: a player is in the pool from the earlier
of his debut and the season's start if he has prior-season minutes, and
from his debut otherwise. Nothing about how he does that season, that
season's final totals, or the end-of-season snapshot files ever reaches
a feature.
"""
import os
import sys
import json
import numpy as np
import pandas as pd

from .config import SEASONS, POSITIONS, ETYPE, WINDOWS
from . import injuries

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from schwaddy.panel import draft_points          # noqa: E402
from schwaddy.lineup import PLAY_FLOOR, UNKNOWN_PLAYER   # noqa: E402

GOALS_CENTRE = 1.4
HOME_ADV = 0.11          # league-average home lift on goals, either way
SHRINK_K = 10.0          # matches of prior weight in the shrunk mean
TEAM_PRIOR_W = 8.0       # matches of prior weight in a club's goal rates
PLAY_WINDOW = 8          # club matches in the availability window
CACHE_VERSION = 7

FEATURE_NAMES = (
    ["pos_" + p for p in POSITIONS]
    + ["gw_frac", "dc_avail", "xg_avail", "played_frac", "log_matches"]
    + [f"{k}_{w}" for w in WINDOWS for k in ("ppm", "avail", "mins")]
    + ["xgi90_6", "xgi90_38", "bps90_6", "bps90_38", "bonus_38",
       "cs_38", "saves90_38", "dc90_12"]
    + ["prev_ppm", "prev_apps", "has_prev"]
    + ["price", "owned", "has_market"]
    + ["price_chg1", "price_chg4", "price_vs_start", "price_pct_pos",
       "own_chg4", "net_xfer1", "net_xfer4"]
    + ["p_play", "team_att", "team_def"]
    + ["inj_factor", "inj_out", "inj_doubt", "inj_days", "inj_stale",
       "inj_known"]
    + ["n_fix1", "home1", "opp_att1", "opp_def1"]
    + ["n_fix5", "opp_att5", "opp_def5"]
    + ["exp_cs1", "exp_c2_1", "exp_t2_1", "exp_cs5", "exp_c2_5", "exp_t2_5"]
    + ["fm1", "fm2", "fm5", "fm_slope", "rest_days", "matches_14d"]
    + [f"x_{p}_{k}" for k in ("att", "def") for p in POSITIONS]
    + ["base_ppm", "base_ep1", "base_next5", "bias"]
)
N_FEATURES = len(FEATURE_NAMES)

# raw per-match quantities carried through the prefix sums
# The archive carries a row for every REGISTERED player in every
# gameweek, not just for those who played, so "app" separates the two and
# a player's first row dates his registration - which is public at the
# time, and is what the pool is built from.
STATS = ("pts", "mins", "app", "start", "xgi", "bps", "bonus", "cs",
         "saves", "dc", "xfer")


def _num(df, col, default=0.0):
    if col not in df.columns:
        return np.full(len(df), default, float)
    return pd.to_numeric(df[col], errors="coerce").fillna(default).to_numpy(float)


class SeasonData:
    """Everything one season contributes, on a timestamp clock."""

    def __init__(self, season, cfg, prev=None, truncate_gw=None,
                 extra_fixtures=None, roster=None):
        """truncate_gw drops every match row after that gameweek, which is
        how selftest.py proves the build is non-anticipative: features for
        the gameweeks before the cut must come out bit-identical."""
        self.season = season
        self.cfg = cfg
        self.truncate_gw = truncate_gw
        self.cut_ts = float("inf")
        self._load(cfg.data_dir, season, prev)
        if extra_fixtures:
            self._add_fixtures(extra_fixtures)
        if roster:
            self._merge_roster(roster, prev)

    # ---------------------------------------------------------------- load
    def _load(self, data_dir, season, prev):
        gws = pd.read_csv(f"{data_dir}/gws_{season}.csv", low_memory=False)
        raw = pd.read_csv(f"{data_dir}/players_raw_{season}.csv",
                          low_memory=False)
        teams = pd.read_csv(f"{data_dir}/teams_{season}.csv", low_memory=False)
        S = json.load(open(f"{data_dir}/draft_bootstrap.json"))["settings"]["scoring"]

        # stable player code, and position, from the season's element list.
        # players_raw is an end-of-season snapshot, so ONLY the identity
        # columns (id, code, element_type) are read from it - never a stat.
        id2code = dict(zip(raw["id"].astype(int), raw["code"].astype(int)))
        # element_type 5 is a manager (2024/25 only); not a draft asset
        id2pos = {int(i): ETYPE[int(t)]
                  for i, t in zip(raw["id"], raw["element_type"])
                  if int(t) in ETYPE}
        # indexed by team id - 1, because that is what the archive's
        # opponent_team column and the fixture list both use
        tmax = int(teams["id"].max())
        self.team_names = [""] * tmax
        for i, nm in zip(teams["id"], teams["name"]):
            self.team_names[int(i) - 1] = nm
        self.team_idx = {nm: int(i) - 1 for i, nm in zip(teams["id"],
                                                        teams["name"])}

        gws = gws[pd.to_numeric(gws["GW"], errors="coerce").notna()].copy()
        gws["GW"] = gws["GW"].astype(float).astype(int)
        gws = gws[(gws["GW"] >= 1) & (gws["GW"] <= 38)]
        gws = gws[gws["element"].astype(int).isin(id2pos)]
        if self.truncate_gw is not None:
            gws = gws[gws["GW"] <= self.truncate_gw]
        gws["ts"] = pd.to_datetime(gws["kickoff_time"], errors="coerce",
                                   utc=True)
        gws = gws[gws["ts"].notna()].sort_values("ts").reset_index(drop=True)

        elem = gws["element"].astype(int).to_numpy()
        codes = np.array([id2code.get(e, -e) for e in elem])
        pos = np.array([id2pos[e] for e in elem], dtype=object)

        self.codes = np.array(sorted(set(codes.tolist())))
        self.row_of = {c: i for i, c in enumerate(self.codes)}
        n = len(self.codes)
        self.n = n
        pidx = np.array([self.row_of[c] for c in codes])

        # per-match draft points under the live season's scoring
        recs = gws.to_dict("records")
        pts = np.array([draft_points(r, p, S) for r, p in zip(recs, pos)])

        mins = _num(gws, "minutes")
        starts = (_num(gws, "starts") if "starts" in gws.columns
                  else (mins >= 60).astype(float))
        xgi = _num(gws, "expected_goals") + _num(gws, "expected_assists")
        stat = dict(
            pts=pts, mins=mins, app=(mins > 0).astype(float), start=starts, xgi=xgi,
            bps=_num(gws, "bps"), bonus=_num(gws, "bonus"),
            xfer=_num(gws, "transfers_balance"),
            cs=_num(gws, "clean_sheets"), saves=_num(gws, "saves"),
            dc=_num(gws, "defensive_contribution"))

        self.has_xg = "expected_goals" in gws.columns
        self.has_dc = "defensive_contribution" in gws.columns

        # position of each player row (mode over his matches)
        self.pos = np.zeros(n, int)
        for i, c in enumerate(codes):
            self.pos[pidx[i]] = POSITIONS.index(pos[i]) if pos[i] in POSITIONS else 2

        # resolution-independent seconds since epoch (pandas 2 and 3)
        ts = gws["ts"].to_numpy("datetime64[s]").astype("int64").astype(float)
        if self.truncate_gw is not None and len(ts):
            # an archive truncated at a gameweek is an archive truncated at
            # a MOMENT, and the injury log has to be cut at the same one or
            # the leakage test would not cover it
            self.cut_ts = float(ts.max())
        gw = gws["GW"].to_numpy()
        self.max_gw = int(gw.max())

        # ---- team schedule and team-level results, on the same clock ----
        tname = gws["team"].to_numpy()
        self.team_of = np.zeros(n, int)
        home = gws["was_home"].astype(str).str.lower().isin(
            ("true", "1")).to_numpy()
        opp = _num(gws, "opponent_team").astype(int)
        hs, as_ = _num(gws, "team_h_score"), _num(gws, "team_a_score")

        fixtures = {}          # (team, gw) -> list of (opp_idx, home, ts)
        tmatch = {}            # team -> list of (ts, gf, ga)
        for k in range(len(gws)):
            t = self.team_idx.get(tname[k])
            if t is None:
                continue
            self.team_of[pidx[k]] = t
            key = (t, int(gw[k]))
            o = opp[k] - 1
            rec = (o, bool(home[k]), ts[k])
            lst = fixtures.setdefault(key, [])
            if rec not in lst:
                lst.append(rec)
            gf, ga = (hs[k], as_[k]) if home[k] else (as_[k], hs[k])
            tmatch.setdefault(t, {})[ts[k]] = (gf, ga, 1.0 if home[k] else 0.0)
        self.fixtures = fixtures
        self.team_matches = {t: np.array(sorted(v.items()), dtype=object)
                             for t, v in tmatch.items()}
        self._team_series = {
            t: (np.array(sorted(v)), np.array([v[k] for k in sorted(v)]))
            for t, v in tmatch.items()}

        # ---- decision times: deadline (first kick-off - 90 min) - lead ----
        first_ko = np.full(39, np.nan)
        for g in range(1, 39):
            m = ts[gw == g]
            if len(m):
                first_ko[g] = m.min()
        # a gameweek nobody plays in (shouldn't happen historically) takes
        # the previous gameweek's clock plus a week
        for g in range(1, 39):
            if np.isnan(first_ko[g]):
                first_ko[g] = (first_ko[g - 1] + 7 * 86400 if g > 1
                               else np.nanmin(ts))
        self.deadline = first_ko - 90 * 60
        self.t_dec = self.deadline - self.cfg.waiver_lead_hours * 3600.0

        # ---- per-player chronological match arrays ----
        order = np.lexsort((ts, pidx))
        self.p_ts = [None] * n
        self.p_cum = [None] * n
        self.p_gw = [None] * n
        self.p_team = [None] * n
        starts_at = np.searchsorted(pidx[order], np.arange(n + 1))
        for i in range(n):
            sl = order[starts_at[i]:starts_at[i + 1]]
            self.p_ts[i] = ts[sl]
            self.p_gw[i] = gw[sl]
            self.p_team[i] = np.array([self.team_idx.get(x, 0) for x in tname[sl]])
            if len(sl):
                # the club he was REGISTERED with, not the one he ends the
                # season at: the row loop above assigns last-wins, which
                # for a January transfer is a club nobody knew about yet
                self.team_of[i] = int(self.p_team[i][0])
            arr = np.stack([stat[s][sl] for s in STATS], axis=1)
            self.p_cum[i] = np.vstack([np.zeros(len(STATS)), np.cumsum(arr, 0)])

        # realized per-gameweek totals (doubles summed)  [before the pool]
        self.real = np.zeros((n, 38))
        self.minutes = np.zeros((n, 38))
        np.add.at(self.real, (pidx, gw - 1), pts)
        np.add.at(self.minutes, (pidx, gw - 1), mins)
        self.played = self.minutes > 0

        # registration gameweek (first row) and debut gameweek (first
        # row with minutes). The pool uses registration; the debut is kept
        # for reporting only, since it is not known in advance.
        self.reg_gw = np.full(n, 99)
        self.debut_gw = np.full(n, 99)
        for i in range(n):
            if len(self.p_gw[i]):
                self.reg_gw[i] = int(self.p_gw[i][0])
        for i in range(n):
            pl = np.flatnonzero(self.played[i])
            if len(pl):
                self.debut_gw[i] = int(pl[0]) + 1

        # market, point-in-time: the value/ownership on the last row before t
        self.p_value = [None] * n
        self.p_sel = [None] * n
        val, sel = _num(gws, "value", 45.0), _num(gws, "selected")
        for i in range(n):
            sl = order[starts_at[i]:starts_at[i + 1]]
            self.p_value[i] = val[sl]
            self.p_sel[i] = sel[sl]

        # ---- previous season, for priors ----
        self.prev_ppm = np.zeros(n)
        self.prev_apps = np.zeros(n)
        self.has_prev = np.zeros(n, bool)
        if prev is not None:
            for i, c in enumerate(self.codes):
                j = prev.row_of.get(c)
                if j is None:
                    continue
                pl = prev.played[j]
                if pl.sum() == 0:
                    continue
                self.has_prev[i] = True
                self.prev_apps[i] = pl.sum()
                self.prev_ppm[i] = prev.real[j][pl].sum() / pl.sum()
        self.prev_team_rate = (prev.final_team_rates() if prev is not None
                               else None)
        self.prev_team_rate_v = (prev.final_team_rates_venue()
                                 if prev is not None else None)

    # ------------------------------------------------------------ team form
    def final_team_rates(self):
        out = {}
        for t, (tss, gfa) in self._team_series.items():
            if len(gfa):
                out[self.team_names[t]] = (float(gfa[:, 0].mean()),
                                           float(gfa[:, 1].mean()))
        return out

    def final_team_rates_venue(self):
        out = {}
        for t, (tss, gfa) in self._team_series.items():
            for v in (1.0, 0.0):
                m = gfa[:, 2] == v
                if m.sum() >= 3:
                    out[(self.team_names[t], v)] = (float(gfa[m, 0].mean()),
                                                    float(gfa[m, 1].mean()))
        return out

    def team_rate_before(self, t, tcut, venue=None):
        """(scored, conceded) per match for club t, matches before tcut,
        shrunk toward last season's rate (a promoted-club prior if new).

        venue 1.0 / 0.0 gives the club's HOME or AWAY rates: home
        advantage is about a tenth of a goal each way and a pooled rate
        hides it. The venue prior is last season's venue rate where the
        club has one, else the pooled rate lifted or lowered by the
        league's average home advantage.
        """
        if venue is not None:
            pooled = self.team_rate_before(t, tcut)
            prior = None
            if self.prev_team_rate_v is not None:
                prior = self.prev_team_rate_v.get((self.team_names[t], venue))
            if prior is None:
                adj = 1 + HOME_ADV if venue == 1.0 else 1 - HOME_ADV
                prior = (pooled[0] * adj, pooled[1] / adj)
            ser = self._team_series.get(t)
            if ser is None:
                return prior
            tss, gfa = ser
            k = int(np.searchsorted(tss, tcut, "left"))
            sel = gfa[:k][gfa[:k, 2] == venue] if k else gfa[:0]
            n = len(sel)
            w = TEAM_PRIOR_W
            return ((sel[:, 0].sum() + w * prior[0]) / (n + w),
                    (sel[:, 1].sum() + w * prior[1]) / (n + w))
        prior = None
        if self.prev_team_rate is not None:
            prior = self.prev_team_rate.get(self.team_names[t])
        if prior is None:
            prior = self._promoted_prior()
        ser = self._team_series.get(t)
        if ser is None:
            return prior
        tss, gfa = ser
        k = int(np.searchsorted(tss, tcut, "left"))
        n = k
        gf = gfa[:k, 0].sum() if k else 0.0
        ga = gfa[:k, 1].sum() if k else 0.0
        w = TEAM_PRIOR_W
        return ((gf + w * prior[0]) / (n + w), (ga + w * prior[1]) / (n + w))

    def _promoted_prior(self):
        if not hasattr(self, "_prom"):
            if self.prev_team_rate:
                sc = [v[0] for v in self.prev_team_rate.values()]
                cc = [v[1] for v in self.prev_team_rate.values()]
                self._prom = (float(np.quantile(sc, 0.2)),
                              float(np.quantile(cc, 0.8)))
            else:
                self._prom = (GOALS_CENTRE, GOALS_CENTRE)
        return self._prom

    # ------------------------------------------------------------- windows
    def _club_at(self, i, k):
        return int(self.p_team[i][k - 1]) if k > 0 else int(self.team_of[i])

    def _fmult(self, pos, opp_att, opp_def):
        """Fixture multiplier on a per-match rate. A keeper or defender
        cares about the opponent's attack, a forward about its defence,
        a midfielder about both."""
        a = np.sqrt(GOALS_CENTRE / max(opp_att, 0.3))
        d = np.sqrt(max(opp_def, 0.3) / GOALS_CENTRE)
        m = {0: a, 1: a, 2: np.sqrt(a * d), 3: d}[pos]
        return float(np.clip(m, 0.75, 1.35))

    def _fix_terms(self, club, g, t, known=True):
        """One gameweek's fixtures for a club, as at time t.

        Returns (n_fixtures, home share, mean opp attack, mean opp
        defence, expected clean sheets, expected 2+ conceded, expected 2+
        scored, kick-offs). The three expectations are Poisson, from the
        club's and the opponent's VENUE-SPLIT rates - a clean sheet is a
        step function of goals conceded, and a defender's points with it,
        so the probability is the feature and not the rate behind it.

        known=False is the schedule as published rather than as played:
        one fixture. If the archive has exactly one it is that one; if it
        has two, the first (the other belonged to another gameweek); if
        none, a neutral opponent at a neutral venue. That is what a
        manager beyond the fixture horizon actually knew.
        """
        fx = self.fixtures.get((club, g), [])
        if not known:
            fx = fx[:1] if fx else [(-1, None, np.nan)]
        if not fx:
            return (0, 0.0, GOALS_CENTRE, GOALS_CENTRE, 0.0, 0.0, 0.0, [])
        att, dfn, hm, kos = [], [], [], []
        cs = c2 = t2 = 0.0
        for o, home, ko in fx:
            if home is None:
                oa, od = GOALS_CENTRE, GOALS_CENTRE
                my_a, my_d = self.team_rate_before(club, t)
                hv = 0.5
            else:
                v_me, v_op = (1.0, 0.0) if home else (0.0, 1.0)
                if 0 <= o < len(self.team_names):
                    oa, od = self.team_rate_before(o, t, v_op)
                else:
                    oa, od = GOALS_CENTRE, GOALS_CENTRE
                my_a, my_d = self.team_rate_before(club, t, v_me)
                hv = 1.0 if home else 0.0
            la = max(my_d * oa / GOALS_CENTRE, 0.05)
            lf = max(my_a * od / GOALS_CENTRE, 0.05)
            cs += np.exp(-la)
            c2 += 1.0 - np.exp(-la) * (1.0 + la)
            t2 += 1.0 - np.exp(-lf) * (1.0 + lf)
            att.append(oa); dfn.append(od); hm.append(hv); kos.append(ko)
        return (len(fx), float(np.mean(hm)), float(np.mean(att)),
                float(np.mean(dfn)), float(cs), float(c2), float(t2), kos)



    # ------------------------------------------------------- live-season use
    def _add_fixtures(self, rows):
        """Merge the published fixture list (data/fixtures_*.json) so that
        the live season knows its schedule past the last match played.

        A fixture list is published before a ball is kicked, so reading it
        for a future gameweek is not hindsight; a RESULT from it never is
        read, only (event, teams, kick-off).
        """
        ko = {}
        for r in rows:
            g, t = r.get("event"), r.get("kickoff_time")
            if not g or not t:
                continue
            g = int(g)
            ts = float(np.datetime64(t.replace("Z", ""), "s").astype("int64"))
            h, a = int(r["team_h"]) - 1, int(r["team_a"]) - 1
            for me, opp, home in ((h, a, True), (a, h, False)):
                lst = self.fixtures.setdefault((me, g), [])
                if not any(x[0] == opp and x[1] == home for x in lst):
                    lst.append((opp, home, ts))
            ko.setdefault(g, []).append(ts)
        # the published list is authoritative: it beats the archive's
        # first-kick-off rule, and beats the week-apart interpolation
        # used for a gameweek that has not been played yet
        for g, v in ko.items():
            if 1 <= g <= 38:
                self.deadline[g] = min(v) - 90 * 60
        self.max_gw = max(self.max_gw, max(ko) if ko else 0)
        self.t_dec = self.deadline - self.cfg.waiver_lead_hours * 3600.0

    def set_deadlines(self, deadlines):
        """Official deadline and waiver times, straight from the draft API.

        FPL sets a deadline 90 minutes before the first kick-off, but it
        can be moved, and the waiver window is published separately - so
        when the game tells us, we believe the game rather than the rule.
        deadlines: gw -> (deadline_ts, waiver_ts or None)
        """
        for g, (dl, wv) in deadlines.items():
            if 1 <= g <= 38:
                self.deadline[g] = dl
                self.t_dec[g] = (wv if wv is not None
                                 else dl - self.cfg.waiver_lead_hours * 3600.0)

    def _merge_roster(self, roster, prev):
        """Add players who are registered in the live game but have no
        match row yet - a mid-season signing on the day he is announced.

        roster: code -> dict(pos=..., team=<team id>, reg_gw=int)
        """
        add = [c for c in roster if c not in self.row_of]
        if not add:
            return
        k = self.n
        self.codes = np.concatenate([self.codes, np.array(add)])
        for j, c in enumerate(add):
            self.row_of[int(c)] = k + j
        z = np.zeros((len(add), 38))
        self.real = np.vstack([self.real, z])
        self.minutes = np.vstack([self.minutes, z])
        self.played = self.minutes > 0
        self.pos = np.concatenate(
            [self.pos, [POSITIONS.index(roster[c]["pos"]) for c in add]])
        self.team_of = np.concatenate(
            [self.team_of, [int(roster[c]["team"]) - 1 for c in add]])
        def reg(c):
            r = roster[c]
            if r.get("added_ts") is not None:
                # registered from the first deadline after the game added him
                g = int(np.searchsorted(self.deadline[1:39], r["added_ts"],
                                        "right")) + 1
                return min(g, 38)
            return int(r.get("reg_gw", 1))
        self.reg_gw = np.concatenate([self.reg_gw, [reg(c) for c in add]])
        self.debut_gw = np.concatenate([self.debut_gw, np.full(len(add), 99)])
        for c in add:
            self.p_ts.append(np.zeros(0))
            self.p_gw.append(np.zeros(0, int))
            self.p_team.append(np.zeros(0, int))
            self.p_cum.append(np.zeros((1, len(STATS))))
            self.p_value.append(np.zeros(0))
            self.p_sel.append(np.zeros(0))
        pp = np.zeros(len(add)); pa = np.zeros(len(add))
        hp = np.zeros(len(add), bool)
        if prev is not None:
            for j, c in enumerate(add):
                i = prev.row_of.get(int(c))
                if i is None:
                    continue
                pl = prev.played[i]
                if pl.sum() == 0:
                    continue
                hp[j] = True
                pa[j] = pl.sum()
                pp[j] = prev.real[i][pl].sum() / pl.sum()
        self.prev_ppm = np.concatenate([self.prev_ppm, pp])
        self.prev_apps = np.concatenate([self.prev_apps, pa])
        self.has_prev = np.concatenate([self.has_prev, hp])
        self.n = len(self.codes)


def build_features(sd, cfg=None, times=None, _shared=None):
    """Feature tensor and heuristic baselines for one season, on one clock.

    times is the decision time per gameweek. There are two of them in a
    week and they are not interchangeable:

      sd.t_dec     the waiver deadline, a day before the gameweek's. What
                   a waiver claim is written on.
      sd.deadline  the gameweek deadline itself. What the free-agency
                   window and the team sheet are decided on - a day of
                   team news later, and after everyone's waivers have
                   already landed.

    build_season() calls this twice and returns both.
    """
    cfg = cfg or sd.cfg
    times = sd.t_dec if times is None else times
    n, F = sd.n, N_FEATURES
    X = np.zeros((n, 38, F), np.float32)
    base_ppm = np.zeros((n, 38), np.float32)
    base_ep1 = np.zeros((n, 38), np.float32)
    base_next5 = np.zeros((n, 38), np.float32)
    p_play = np.zeros((n, 38), np.float32)
    pool = np.zeros((n, 38), bool)

    si = {s: i for i, s in enumerate(STATS)}
    # Position prior for the shrunk mean: last season's positional average
    # over the players REGISTERED BY THAT GAMEWEEK. Averaging over the
    # season's final roster instead would quietly use the knowledge of who
    # is going to be signed in January - a small leak, and exactly the
    # kind selftest.test_non_anticipation exists to catch.
    default = np.array([2.6, 2.7, 2.8, 3.0])
    pos_mean_g = np.tile(default, (39, 1))
    for g in range(1, 39):
        live = sd.reg_gw <= g
        for p in range(4):
            m = live & sd.has_prev & (sd.pos == p) & (sd.prev_apps >= 6)
            if m.sum() >= 5:
                pos_mean_g[g, p] = float(sd.prev_ppm[m].mean())

    prev_avail = np.zeros(n)
    if sd.has_prev.any():
        prev_avail = np.clip(sd.prev_apps / 38.0, 0, 1)

    # the injury log, cut at the same moment as the match archive
    inj = injuries.load(cfg.data_dir, sd.season) if cfg.use_injuries else {}
    if sd.cut_ts != float("inf"):
        inj = {c: a[a[:, 0] <= sd.cut_ts] for c, a in inj.items()}

    # cache of club windows, one per (club, gameweek)
    club_win = {}

    def club_window(c, g, W):
        key = (c, g, W)
        if key in club_win:
            return club_win[key]
        ser = sd._team_series.get(c)
        t = sd.t_dec[g]
        if ser is None:
            out = (t, 0)
        else:
            tss = ser[0]
            k = int(np.searchsorted(tss, t, "left"))
            lo = max(0, k - W)
            out = (float(tss[lo]) if k > lo else t, k - lo)
        club_win[key] = out
        return out

    for i in range(n):
        pts_ts = sd.p_ts[i]
        cum = sd.p_cum[i]
        posi = int(sd.pos[i])
        inj_rows = inj.get(int(sd.codes[i]))
        for g in range(1, 39):
            t = times[g]
            k = int(np.searchsorted(pts_ts, t, "left"))
            club = sd._club_at(i, k)
            col = g - 1
            f = np.zeros(F, np.float32)
            f[posi] = 1.0
            f[4] = g / 38.0
            f[5] = 1.0 if sd.has_dc else 0.0
            f[6] = 1.0 if sd.has_xg else 0.0
            napp = cum[k][si["app"]]
            f[7] = napp / 38.0
            f[8] = np.log1p(napp) / 4.0

            # trailing windows, measured in the CLUB's matches so that a
            # player who has stopped playing is charged for the matches he
            # missed rather than judged on the last ones he started
            j = 9
            for W in WINDOWS:
                t0, ncl = club_window(club, g, W)
                k0 = int(np.searchsorted(pts_ts, t0, "left"))
                s = cum[k] - cum[k0]
                na = s[si["app"]]
                f[j] = (s[si["pts"]] / na) if na else 0.0
                f[j + 1] = (s[si["mins"]] / (90.0 * ncl)) if ncl else 0.0
                f[j + 2] = (s[si["mins"]] / (90.0 * na)) if na else 0.0
                j += 3

            t6, ncl6 = club_window(club, g, 6)
            k6 = int(np.searchsorted(pts_ts, t6, "left"))
            t12, _ = club_window(club, g, 12)
            k12 = int(np.searchsorted(pts_ts, t12, "left"))
            m6 = max(cum[k][si["mins"]] - cum[k6][si["mins"]], 1e-6)
            m38 = max(cum[k][si["mins"]], 1e-6)
            m12 = max(cum[k][si["mins"]] - cum[k12][si["mins"]], 1e-6)
            f[j] = 90 * (cum[k][si["xgi"]] - cum[k6][si["xgi"]]) / m6
            f[j + 1] = 90 * cum[k][si["xgi"]] / m38
            f[j + 2] = 90 * (cum[k][si["bps"]] - cum[k6][si["bps"]]) / m6 / 10
            f[j + 3] = 90 * cum[k][si["bps"]] / m38 / 10
            f[j + 4] = cum[k][si["bonus"]] / max(napp, 1)
            f[j + 5] = cum[k][si["cs"]] / max(napp, 1)
            f[j + 6] = 90 * cum[k][si["saves"]] / m38 / 3
            f[j + 7] = 90 * (cum[k][si["dc"]] - cum[k12][si["dc"]]) / m12 / 10
            j += 8

            f[j] = sd.prev_ppm[i]
            f[j + 1] = sd.prev_apps[i] / 38.0
            f[j + 2] = 1.0 if sd.has_prev[i] else 0.0
            j += 3

            # The market. There is no budget in draft, so a price is not
            # a cost here - it is information. FPL moves a price on net
            # transfers, so the move is a crowd forecast of a player's
            # returns, updated nightly by a few million people, and the
            # opening price is the game's own pre-season expectation of
            # him. That is worth having precisely because it is not
            # derived from the same match data as everything else.
            vals, sels = sd.p_value[i], sd.p_sel[i]
            if k > 0:
                f[j] = vals[k - 1] / 10.0
                f[j + 1] = np.log1p(sels[k - 1]) / 12.0
                f[j + 2] = 1.0
                f[j + 3] = (vals[k - 1] - vals[max(k - 2, 0)]) / 5.0
                f[j + 4] = (vals[k - 1] - vals[max(k - 5, 0)]) / 5.0
                f[j + 5] = (vals[k - 1] - vals[0]) / 10.0
                own = max(sels[k - 1], 1.0)
                x1 = cum[k][si["xfer"]] - cum[k - 1][si["xfer"]]
                x4 = cum[k][si["xfer"]] - cum[max(k - 4, 0)][si["xfer"]]
                # f[j + 6] is the within-position price percentile, which
                # needs every player at once and is filled in below
                f[j + 7] = (np.log1p(sels[k - 1])
                            - np.log1p(sels[max(k - 5, 0)]))
                f[j + 8] = float(np.clip(x1 / own, -1, 1))
                f[j + 9] = float(np.clip(x4 / own, -2, 2))
            elif len(vals):
                # Before he has played, only the OPENING price is fair
                # game: FPL publishes it weeks before any draft, and for a
                # summer signing with no Premier League history it is the
                # only read anybody has on him. Opening ownership is a
                # separate switch because it keeps moving right up to the
                # deadline.
                if cfg.preseason_price:
                    f[j] = vals[0] / 10.0
                    f[j + 2] = 1.0
                if cfg.preseason_market:
                    f[j + 1] = np.log1p(sels[0]) / 12.0
                    f[j + 2] = 1.0
            j += 10

            # availability: club-window minutes share, blended toward last
            # season while the current one is too short to say anything
            _, ncl8 = club_window(club, g, PLAY_WINDOW)
            t8, _ = club_window(club, g, PLAY_WINDOW)
            k8 = int(np.searchsorted(pts_ts, t8, "left"))
            if ncl8 > 0:
                share = (cum[k] - cum[k8])[si["mins"]] / (90.0 * ncl8)
            else:
                share = np.nan
            w_prev = max(0, 4 - ncl8) / 4.0
            if np.isnan(share):
                share = prev_avail[i] if sd.has_prev[i] else np.nan
            elif w_prev > 0 and sd.has_prev[i]:
                share = (1 - w_prev) * share + w_prev * prev_avail[i]
            if np.isnan(share):
                pp = UNKNOWN_PLAYER
            else:
                pp = PLAY_FLOOR + (1 - PLAY_FLOOR) * min(1.0, float(share))
            # what the game itself was advertising about him at time t.
            # This multiplies availability rather than sitting beside it,
            # so the heuristic baseline - and therefore the residual
            # policy's starting point - knows about injuries in training
            # exactly as the live driver does.
            ifac, iout, idbt, idays, istale, iknown = injuries.state_at(
                inj_rows, t)
            pp *= ifac
            p_play[i, col] = pp
            f[j] = pp
            ta, tc = sd.team_rate_before(club, t)
            f[j + 1], f[j + 2] = ta, tc
            j += 3
            f[j] = ifac
            f[j + 1] = iout
            f[j + 2] = idbt
            f[j + 3] = min(idays, 180.0) / 30.0
            f[j + 4] = min(istale, 60.0) / 14.0
            f[j + 5] = iknown
            j += 6

            # the next five gameweeks, known as far as the horizon and as
            # published beyond it
            H = cfg.fixture_horizon
            terms = [sd._fix_terms(club, g + h, t, known=(h <= H))
                     if g + h <= 38 else None for h in range(5)]
            nf1, h1, oa1, od1, cs1, c21, t21, kos1 = terms[0]
            f[j] = nf1; f[j + 1] = h1; f[j + 2] = oa1; f[j + 3] = od1
            j += 4
            live5 = [x for x in terms if x and x[0]]
            f[j] = sum(x[0] for x in terms if x) / 5.0
            f[j + 1] = float(np.mean([x[2] for x in live5])) if live5 else GOALS_CENTRE
            f[j + 2] = float(np.mean([x[3] for x in live5])) if live5 else GOALS_CENTRE
            j += 3
            # clean-sheet mechanics: the step functions a defender and a
            # forward are actually paid on, this week and over five
            f[j] = cs1; f[j + 1] = c21; f[j + 2] = t21
            f[j + 3] = sum(x[4] for x in terms if x) / 5.0
            f[j + 4] = sum(x[5] for x in terms if x) / 5.0
            f[j + 5] = sum(x[6] for x in terms if x) / 5.0
            j += 6
            # the shape of the run: the position-aware multiplier over
            # one, two and five, and near minus far - a waiver is a
            # decision about a horizon you can revisit next week
            fm = [(x[0] * sd._fmult(posi, x[2], x[3])) if x else 0.0
                  for x in terms]
            f[j] = fm[0]
            f[j + 1] = float(np.mean(fm[:2]))
            f[j + 2] = float(np.mean(fm))
            f[j + 3] = float(np.mean(fm[:2]) - np.mean(fm[2:]))
            j += 4
            # congestion, from kick-off times alone: rest before the next
            # match, and matches in the fortnight ahead
            ser = sd._team_series.get(club)
            last_ts = np.nan
            if ser is not None:
                kc = int(np.searchsorted(ser[0], t, "left"))
                if kc:
                    last_ts = float(ser[0][kc - 1])
            nxt = [ko for ko in kos1 if not np.isnan(ko)]
            if nxt and not np.isnan(last_ts):
                f[j] = min((min(nxt) - last_ts) / 86400.0, 14.0) / 7.0
            else:
                f[j] = 1.0
            soon = [ko for x in terms[:H + 1] if x for ko in x[7]
                    if not np.isnan(ko) and t <= ko <= t + 14 * 86400]
            f[j + 1] = len(soon) / 3.0
            j += 2
            # position x opponent, zeroed unless switched on
            if cfg.pos_interact:
                f[j + posi] = oa1 - GOALS_CENTRE
                f[j + 4 + posi] = od1 - GOALS_CENTRE
            j += 8

            # ---- heuristic baselines, the residual policy's starting point
            prior = (sd.prev_ppm[i] if sd.has_prev[i]
                     else pos_mean_g[g, posi])
            bp = (cum[k][si["pts"]] + SHRINK_K * prior) / (napp + SHRINK_K)
            base_ppm[i, col] = bp
            ep1 = bp * pp * nf1 * sd._fmult(posi, oa1, od1)
            base_ep1[i, col] = ep1
            tot5 = sum(bp * pp * x[0] * sd._fmult(posi, x[2], x[3])
                       for x in terms if x)
            base_next5[i, col] = tot5
            f[j] = bp; f[j + 1] = ep1; f[j + 2] = tot5 / 5.0; f[j + 3] = 1.0
            X[i, col] = f

        # ---- pool: registration is public at the time, form never is
        reg = sd.reg_gw[i]
        for g in range(1, 39):
            live = reg <= g
            pool[i, g - 1] = live and (bool(sd.has_prev[i])
                                       if cfg.pool_mode == "history" else True)

    # Within-position price percentile. A raw price is not comparable
    # across seasons - the game inflates - and "is he a premium" is a
    # statement about his position's market, not about pounds. Ranked
    # among the players actually in the pool that gameweek.
    pi = FEATURE_NAMES.index("price")
    for g in range(38):
        live = pool[:, g]
        for pz in range(4):
            m = live & (sd.pos == pz)
            if m.sum() < 3:
                continue
            v = X[m, g, pi]
            r = np.argsort(np.argsort(v)) / max(len(v) - 1, 1)
            X[m, g, pi + 3 + 3] = r.astype(np.float32)

    exp_apps = np.where(sd.has_prev,
                        38 * (0.35 + 0.65 * np.minimum(1.0, sd.prev_apps / 38.0)),
                        38 * 0.20)
    prior_ppm = np.where(sd.has_prev, sd.prev_ppm, pos_mean_g[1][sd.pos])
    # a player carrying an injury on draft day is worth less on draft day
    draft_fac = np.array([injuries.state_at(inj.get(int(c)), times[1])[0]
                          for c in sd.codes])
    base_season = (prior_ppm * exp_apps * draft_fac).astype(np.float32)

    return dict(X=X, base_ppm=base_ppm, base_ep1=base_ep1,
                base_next5=base_next5, base_season=base_season,
                p_play=p_play, pool=pool, real=sd.real.astype(np.float32),
                minutes=sd.minutes.astype(np.float32),
                pos=sd.pos.astype(np.int8), codes=sd.codes,
                team=sd.team_of.astype(np.int16))


def build_season(sd, cfg=None):
    """Both of the week's clocks, in one set of arrays.

    A gameweek asks a manager for three things at two different moments,
    and the archive supports telling them apart:

      waiver deadline   (deadline - 24h)  the ranked waiver claims
      gameweek deadline                   free agency, then the team sheet

    Everything suffixed _dl is the second of those. It is a day of team
    news later than the first and it comes after every manager's waivers
    have already been processed, which is exactly why the free-agency
    scramble is a different decision from the waiver and not a repeat of
    it.
    """
    cfg = cfg or sd.cfg
    a = build_features(sd, cfg, times=sd.t_dec)
    b = build_features(sd, cfg, times=sd.deadline)
    for k in ("X", "base_ppm", "base_ep1", "base_next5", "p_play"):
        a[k + "_dl"] = b[k]
    return a


# ---------------------------------------------------------------- caching
def _key(cfg):
    return (f"v{CACHE_VERSION}_{cfg.pool_mode}_m{int(cfg.preseason_market)}"
            f"_i{int(cfg.use_injuries)}"
            f"_p{int(cfg.preseason_price)}"
            f"_h{cfg.fixture_horizon}_x{int(cfg.pos_interact)}"
            f"_w{cfg.waiver_lead_hours:g}")


def season_arrays(season, cfg, prev_season=None, rebuild=False):
    """Feature arrays for one season, from cache when possible."""
    os.makedirs(cfg.cache_dir, exist_ok=True)
    path = os.path.join(cfg.cache_dir, f"{season}_{_key(cfg)}.npz")
    if os.path.exists(path) and not rebuild:
        z = np.load(path, allow_pickle=False)
        return {k: z[k] for k in z.files}
    prev = SeasonData(prev_season, cfg) if prev_season else None
    sd = SeasonData(season, cfg, prev=prev)
    d = build_season(sd, cfg)
    np.savez_compressed(path, **d)
    return d


def load_seasons(cfg, seasons=None, rebuild=False, verbose=False):
    """{season: arrays} for every requested season, prior season attached
    so that last-season priors exist wherever the archive allows."""
    seasons = list(seasons or SEASONS)
    out = {}
    for s in seasons:
        i = SEASONS.index(s)
        prev = SEASONS[i - 1] if i > 0 else None
        if verbose:
            print(f"  features {s}" + (f" (prior {prev})" if prev else ""),
                  flush=True)
        out[s] = season_arrays(s, cfg, prev, rebuild)
    return out


class Standardizer:
    """Feature scaling, fitted on TRAINING seasons only.

    Fitting it on everything would leak the validation season's league-wide
    distribution into the model that is scored on it - a small leak, but
    the kind that makes a cross-validation number a lie.
    """

    def __init__(self, mean=None, sd=None):
        self.mean, self.sd = mean, sd

    def fit(self, arrays):
        cells = []
        for d in arrays:
            X, pool = d["X"], d["pool"]
            cells.append(X[pool])
        A = np.concatenate(cells, 0)
        self.mean = A.mean(0)
        self.sd = A.std(0)
        self.sd[self.sd < 1e-6] = 1.0
        # the bias column stays exactly 1
        self.mean[-1], self.sd[-1] = 0.0, 1.0
        return self

    def transform(self, X):
        return np.clip((X - self.mean) / self.sd, -8, 8).astype(np.float32)

    def save(self, path):
        np.savez(path, mean=self.mean, sd=self.sd)

    @staticmethod
    def load(path):
        z = np.load(path)
        return Standardizer(z["mean"], z["sd"])

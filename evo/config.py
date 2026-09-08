"""Shared constants and the knobs every module reads.

Anything that changes what the model sees or does lives here, so that a
run can be reproduced from a single dict written into its checkpoint.
"""
from dataclasses import dataclass, field, asdict

SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
LIVE_SEASON = "2026-27"

SQUAD = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
SQUAD_SIZE = 15
POSITIONS = ("GKP", "DEF", "MID", "FWD")
ETYPE = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
# starting XI bounds, from draft_bootstrap settings.squad
MIN_PLAY = {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}
MAX_PLAY = {"GKP": 1, "DEF": 5, "MID": 5, "FWD": 3}
N_MANAGERS = 6

# trailing windows, in matches the player's club has played
WINDOWS = (3, 6, 12, 38)


@dataclass
class Config:
    # --- data / leakage ---
    data_dir: str = "data"
    cache_dir: str = "evo/cache"
    # "membership": a player is in the pool if he is on a Premier League
    # roster that season (known to a real manager at the time) and has
    # either prior-season minutes or has already debuted. "history": only
    # players with prior-season minutes, which is stricter and drops
    # every summer signing.
    pool_mode: str = "membership"
    # the market's pre-season read (opening price, opening ownership) is
    # published days after a typical draft; off by default so the draft
    # board is built from football alone.
    preseason_market: bool = False
    # injury and availability history, harvested by evo/injuries.py from
    # the archive repo's git history. Off is the ablation: it is what the
    # model looked like before there was any, and the honest comparison
    # for whether the data earned its place.
    use_injuries: bool = True
    # bookmaker ratings exist for the five archive seasons but not for
    # the live one, and a feature the live model cannot compute is worse
    # than no feature at all. Turn on once odds_2026-27.csv exists.
    use_odds: bool = False
    # waivers are decided this many hours before the deadline; the same
    # information set is used for the line-up, which is conservative for
    # the line-up rather than optimistic for the waiver.
    waiver_lead_hours: float = 24.0

    # --- policy ---
    hidden: int = 24
    # residual policy: score = baseline + scale * net(features). The
    # heuristic baseline is the starting point, so a fresh genome plays
    # about as well as the repo's shrunk-mean manager and evolution only
    # has to find what improves on it. residual=False scores on the net
    # alone, which is the harder, purer experiment.
    residual: bool = True
    init_scale: float = 0.05

    # --- league mechanics ---
    waiver_first_gw: int = 2
    max_claims: int = 3          # ranked claims submitted per manager
    max_success_per_gw: int = 1  # successful transactions per manager
    draft_shortlist: int = 80    # candidates scored per draft pick
    waiver_shortlist: int = 60   # free agents scored per waiver window

    # --- evolution ---
    pop_size: int = 200
    generations: int = 150
    elite: int = 5
    truncation: float = 0.25
    p_crossover: float = 0.3
    sigma0: float = 0.08
    # complexity penalty subtracted from fitness, l2 * mean(genome^2).
    # Off by default. It is the lever to reach for when the cross-
    # validation curve shows a large gap between the paired score on the
    # training seasons and the one on the held-out season, which is the
    # failure mode a population this size actually has. Choose it the
    # same way as everything else here - by the validation curve, never
    # by eye on the training one.
    l2: float = 0.0
    leagues_per_genome: int = 8
    hof_size: int = 24
    hof_every: int = 5
    anchor_seats: int = 2        # seats per league given to heuristics/HOF
    seed: int = 0
    workers: int = 0             # 0 = os.cpu_count()

    train_seasons: tuple = field(default_factory=lambda: tuple(SEASONS[:-1]))
    valid_seasons: tuple = field(default_factory=lambda: (SEASONS[-1],))

    def to_dict(self):
        d = asdict(self)
        d["train_seasons"] = list(self.train_seasons)
        d["valid_seasons"] = list(self.valid_seasons)
        return d

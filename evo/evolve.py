"""Neuroevolution: truncation selection, self-adaptive Gaussian mutation,
uniform crossover, elitism, and a hall of fame.

Why evolution and not gradients. The action space is combinatorial (a
draft pick out of six hundred, an eleven out of fifteen), the reward is
one season total that arrives after a hundred and thirty decisions, and
the opponents move as the population learns. There is no differentiable
path from a weight to that reward. Evolution needs only the score, and it
parallelises across a cluster without any of the machinery.

Self-play, and why the anchors are there. Each league seats genomes from
the population plus cfg.anchor_seats outsiders: half the time a heuristic
manager, half the time a former champion from the hall of fame. Without
the heuristics a population can drift into beating only itself; without
the hall of fame it can cycle, rediscovering a strategy that beats this
generation and loses to the last one.

Fitness is shaped rather than winner-take-all, because one season in a
six-team league is far too noisy to rank two hundred genomes on titles:

    1.0 * won  +  0.5 * (5 - rank)/5  +  0.5 * tanh(margin / 100)

margin being the manager's season total less the league's mean.
"""
import json
import os
import time
import numpy as np

from .config import Config, N_MANAGERS
from .features import Standardizer, load_seasons
from .net import Brain, Heuristic, new_genome, layout
from .sim import SeasonView, simulate

TAU = 0.25          # log-normal step-size self-adaptation


def fitness(totals, seat):
    r = int(np.sum(totals > totals[seat]))
    margin = totals[seat] - totals.mean()
    return 1.0 * (r == 0) + 0.5 * (5 - r) / 5.0 + 0.5 * np.tanh(margin / 100.0)


# ------------------------------------------------------------------ workers
_W = {}


def _init_worker(cfg, seasons, mean, sd):
    std = Standardizer(mean, sd)
    arrays = load_seasons(cfg, seasons)
    _W["cfg"] = cfg
    _W["views"] = {s: SeasonView(s, arrays[s], std) for s in seasons}


def _play(task):
    """One chunk of leagues. Returns (genome index, seat fitness) pairs."""
    cfg, leagues, pop, hof = task
    views = _W["views"]
    out = []
    cache = {}
    # season-major, so a genome's encoder output is computed once per
    # season in this chunk rather than once per league
    for (season, seats, kinds, seed) in sorted(leagues, key=lambda x: x[0]):
        brains = []
        for who, kind in zip(seats, kinds):
            if kind == "pop":
                if who not in cache:
                    cache[who] = Brain(pop[who], cfg)
                brains.append(cache[who])
            elif kind == "hof":
                brains.append(Brain(hof[who], cfg))
            else:
                brains.append(Heuristic(Heuristic.KINDS[who], cfg, seed + who))
        rng = np.random.default_rng(seed)
        totals, _ = simulate(brains, views[season], cfg, rng)
        for m, (who, kind) in enumerate(zip(seats, kinds)):
            if kind == "pop":
                out.append((who, fitness(totals, m)))
    return out


def _chunks(seq, n):
    k = max(1, int(np.ceil(len(seq) / n)))
    return [seq[i:i + k] for i in range(0, len(seq), k)]


class Evolver:
    def __init__(self, cfg, out_dir, seasons=None, standardizer=None):
        self.cfg = cfg
        self.out = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.seasons = list(seasons or cfg.train_seasons)
        arrays = load_seasons(cfg, self.seasons, verbose=True)
        self.std = standardizer or Standardizer().fit(
            [arrays[s] for s in self.seasons])
        self.views = {s: SeasonView(s, arrays[s], self.std)
                      for s in self.seasons}
        self.rng = np.random.default_rng(cfg.seed)
        _, self.glen = layout(cfg)
        self.pop = np.stack([new_genome(cfg, self.rng)
                             for _ in range(cfg.pop_size)])
        self.sigma = np.full(cfg.pop_size, cfg.sigma0, np.float32)
        self.hof = np.zeros((0, self.glen), np.float32)
        self.gen = 0
        self.history = []
        self.pool = None

    # ------------------------------------------------------------ schedule
    def _schedule(self):
        cfg = self.cfg
        seats_pop = N_MANAGERS - cfg.anchor_seats
        leagues = []
        for r in range(cfg.leagues_per_genome):
            # one season per ROUND, not per league: every genome in a
            # round is judged on the same season, so no genome is selected
            # for having drawn an easy one. With leagues_per_genome a
            # multiple of the number of training seasons, each genome
            # plays each season the same number of times.
            season = self.seasons[r % len(self.seasons)]
            perm = self.rng.permutation(cfg.pop_size)
            for i in range(0, cfg.pop_size - seats_pop + 1, seats_pop):
                grp = perm[i:i + seats_pop]
                if len(grp) < seats_pop:
                    break
                seats = list(grp)
                kinds = ["pop"] * seats_pop
                for _ in range(cfg.anchor_seats):
                    if len(self.hof) and self.rng.random() < 0.5:
                        seats.append(int(self.rng.integers(len(self.hof))))
                        kinds.append("hof")
                    else:
                        seats.append(int(self.rng.integers(
                            len(Heuristic.KINDS))))
                        kinds.append("heur")
                o = self.rng.permutation(N_MANAGERS)
                seats = [seats[j] for j in o]
                kinds = [kinds[j] for j in o]
                leagues.append((season, seats, kinds,
                                int(self.rng.integers(1 << 30))))
        return leagues

    # ------------------------------------------------------------ evaluate
    def _ensure_pool(self):
        cfg = self.cfg
        nw = cfg.workers or (os.cpu_count() or 1)
        if nw <= 1:
            _init_worker(cfg, self.seasons, self.std.mean, self.std.sd)
            return None
        if self.pool is None:
            import multiprocessing as mp
            ctx = mp.get_context("fork")
            self.pool = ctx.Pool(nw, initializer=_init_worker,
                                 initargs=(cfg, self.seasons,
                                           self.std.mean, self.std.sd))
            self.nw = nw
        return self.pool

    def evaluate(self):
        leagues = self._schedule()
        pool = self._ensure_pool()
        fits = np.zeros(self.cfg.pop_size)
        cnt = np.zeros(self.cfg.pop_size)
        if pool is None:
            res = [_play((self.cfg, leagues, self.pop, self.hof))]
        else:
            tasks = [(self.cfg, ch, self.pop, self.hof)
                     for ch in _chunks(leagues, self.nw * 2)]
            res = pool.map(_play, tasks)
        for chunk in res:
            for who, f in chunk:
                fits[who] += f
                cnt[who] += 1
        return fits / np.maximum(cnt, 1)

    # --------------------------------------------------------------- breed
    def breed(self, fits):
        cfg = self.cfg
        order = np.argsort(-fits)
        n_par = max(2, int(cfg.truncation * cfg.pop_size))
        parents = order[:n_par]
        pop = np.empty_like(self.pop)
        sig = np.empty_like(self.sigma)
        pop[:cfg.elite] = self.pop[order[:cfg.elite]]
        sig[:cfg.elite] = self.sigma[order[:cfg.elite]]
        for i in range(cfg.elite, cfg.pop_size):
            a = parents[self.rng.integers(n_par)]
            child = self.pop[a].copy()
            s = self.sigma[a]
            if self.rng.random() < cfg.p_crossover:
                b = parents[self.rng.integers(n_par)]
                mask = self.rng.random(self.glen) < 0.5
                child[mask] = self.pop[b][mask]
                s = 0.5 * (s + self.sigma[b])
            s = float(np.clip(s * np.exp(TAU * self.rng.normal()),
                              1e-4, 1.0))
            child += (s * self.rng.normal(size=self.glen)).astype(np.float32)
            pop[i] = child
            sig[i] = s
        self.pop, self.sigma = pop, sig
        return order

    # ----------------------------------------------------------------- run
    def step(self):
        t0 = time.time()
        fits = self.evaluate()
        order = np.argsort(-fits)
        best = self.pop[order[0]].copy()
        rec = dict(gen=self.gen, best=float(fits[order[0]]),
                   mean=float(fits.mean()), sd=float(fits.std()),
                   sigma=float(np.median(self.sigma)),
                   secs=round(time.time() - t0, 1))
        if self.gen % self.cfg.hof_every == 0:
            self.hof = np.concatenate([self.hof, best[None]])[-self.cfg.hof_size:]
        self.breed(fits)
        self.best = best
        self.gen += 1
        self.history.append(rec)
        return rec

    def save(self, tag="ckpt"):
        path = os.path.join(self.out, f"{tag}.npz")
        tmp = path + ".tmp.npz"   # np.savez appends .npz to a bare name
        np.savez(tmp, pop=self.pop, sigma=self.sigma, hof=self.hof,
                 best=getattr(self, "best", self.pop[0]),
                 mean=self.std.mean, sd=self.std.sd, gen=self.gen,
                 cfg=json.dumps(self.cfg.to_dict()),
                 seasons=np.array(self.seasons))
        os.replace(tmp, path)
        with open(os.path.join(self.out, "history.json"), "w") as fh:
            json.dump(self.history, fh, indent=1)

    def load(self, path):
        z = np.load(path, allow_pickle=False)
        self.pop, self.sigma, self.hof = z["pop"], z["sigma"], z["hof"]
        self.best = z["best"]
        self.gen = int(z["gen"])
        hp = os.path.join(self.out, "history.json")
        if os.path.exists(hp):
            self.history = json.load(open(hp))
        return self

    def close(self):
        if self.pool is not None:
            self.pool.close()
            self.pool.join()
            self.pool = None

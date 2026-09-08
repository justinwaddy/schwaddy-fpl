"""The policy: one shared encoder, three decision heads, one flat genome.

    h        = tanh(x W1 + b1)                     shared player encoder
    score_k  = [h, context_k] w_k + b_k            one head per decision

The three decisions are the draft pick, the waiver claim, and the weekly
line-up. They share the encoder because they are the same judgement -
how good is this player, to me, now - asked over different horizons, and
sharing it is what lets ninety draft picks worth of signal train the
line-up head as well.

Residual scoring (cfg.residual, on by default):

    final = baseline + scale_k * unit_k * score_k

The baseline is the heuristic value already used everywhere in this repo:
a shrunk points-per-match, an availability estimate, and a fixture
multiplier. unit_k is the spread of that baseline among the candidates,
so scale_k is dimensionless and a genome initialised near zero plays the
heuristic exactly. Evolution therefore starts from a competent manager
and only has to find what improves on him, which matters enormously when
the reward is one season total arriving after a hundred and thirty
decisions. Set residual=False for the pure-network experiment.

Everything is float32 numpy: no autograd, no GPU, nothing to install on
the cluster beyond what this repo already needs.
"""
import numpy as np

from .config import Config
from .features import N_FEATURES

C_DRAFT = 10
C_WAIVER = 6
C_LINEUP = 0
HEADS = ("draft", "waiver", "lineup")
CONTEXT = dict(draft=C_DRAFT, waiver=C_WAIVER, lineup=C_LINEUP)


def layout(cfg):
    """(slices, total length) of the flat genome."""
    F, H = N_FEATURES, cfg.hidden
    sl, o = {}, 0

    def take(name, size, shape):
        nonlocal o
        sl[name] = (slice(o, o + size), shape)
        o += size

    take("W1", F * H, (F, H))
    take("b1", H, (H,))
    for k in HEADS:
        take(f"w_{k}", H + CONTEXT[k], (H + CONTEXT[k],))
        take(f"b_{k}", 1, (1,))
    take("scale", len(HEADS), (len(HEADS),))
    take("margin", 1, (1,))          # waiver switching margin, in units
    return sl, o


def new_genome(cfg, rng):
    sl, n = layout(cfg)
    g = np.zeros(n, np.float32)
    F, H = N_FEATURES, cfg.hidden
    s, _ = sl["W1"]
    g[s] = rng.normal(0, 1.0 / np.sqrt(F), F * H)
    for k in HEADS:
        s, _ = sl[f"w_{k}"]
        g[s] = rng.normal(0, 1.0 / np.sqrt(H + CONTEXT[k]), s.stop - s.start)
    g[sl["scale"][0]] = cfg.init_scale
    g[sl["margin"][0]] = 0.25
    return g


class Brain:
    """A genome, unpacked, with its encoder output cached per season."""

    def __init__(self, genome, cfg):
        self.cfg = cfg
        self.g = np.asarray(genome, np.float32)
        sl, n = layout(cfg)
        if self.g.size != n:
            raise ValueError(
                f"this genome has {self.g.size} weights and the current "
                f"configuration needs {n} ({N_FEATURES} features, "
                f"{cfg.hidden} hidden). A checkpoint is tied to the feature "
                f"set it was trained on: retrain it, or check out the "
                f"revision it came from.")
        self.p = {k: self.g[s].reshape(sh) for k, (s, sh) in sl.items()}
        self._cache_key = None
        self._H = None

    # ------------------------------------------------------------ encoder
    def encode(self, key, Xn):
        """tanh(X W1 + b1) for a whole season, cached: a genome plays
        several leagues in the same season and the encoder does not
        change between them."""
        if self._cache_key != key:
            n, g, F = Xn.shape
            h = np.tanh(Xn.reshape(-1, F) @ self.p["W1"] + self.p["b1"])
            self._H = h.reshape(n, g, -1).astype(np.float32)
            self._cache_key = key
        return self._H

    # -------------------------------------------------------------- heads
    def raw(self, head, key, Xn, rows, gw, ctx=None):
        """Head output for `rows` at gameweek `gw` (1-based)."""
        H = self.encode(key, Xn)[rows, gw - 1]
        w = self.p[f"w_{head}"]
        nh = H.shape[1]
        out = H @ w[:nh] + self.p[f"b_{head}"][0]
        if ctx is not None and w.shape[0] > nh:
            out = out + ctx @ w[nh:]
        return out

    def score(self, head, key, Xn, rows, gw, baseline, ctx=None,
              feats=None):
        """Residual score in the baseline's own units.

        feats (raw, unscaled) is ignored here and used by Heuristic, so
        that the two managers are interchangeable to the simulator.
        """
        r = self.raw(head, key, Xn, rows, gw, ctx)
        if not self.cfg.residual:
            return r
        k = HEADS.index(head)
        unit = float(np.std(baseline)) if len(baseline) > 1 else 1.0
        unit = unit if unit > 1e-6 else 1.0
        return baseline + self.p["scale"][k] * unit * r

    @property
    def margin(self):
        return float(self.p["margin"][0])


# --------------------------------------------------------------- heuristics
class Heuristic:
    """A fixed manager, used as an anchor so that self-play has something
    outside the population to be measured against.

    Same interface as Brain, but reading RAW features rather than the
    standardized ones - these are rules with units, not weights.

    These stand in for human opponents: the
    market follower, the last-season loyalist, the form chaser. None of
    them is the network, and a population that can only beat itself has
    learned nothing, so validation is always run against these.
    """

    KINDS = ("baseline", "form", "lastseason", "ppg", "market", "noisy")

    def __init__(self, kind, cfg, seed=0):
        self.kind = kind
        self.cfg = cfg
        self.rng = np.random.default_rng(seed)
        self.margin = 0.35 * 5

    def encode(self, key, Xn):
        return None

    def score(self, head, key, Xn, rows, gw, baseline, ctx=None, feats=None):
        f = feats
        if self.kind == "baseline":
            return baseline
        if self.kind == "noisy":
            return baseline * self.rng.normal(1.0, 0.35, len(baseline))
        scale = float(np.mean(baseline)) or 1.0
        if self.kind == "form":
            v = f[:, IDX["ppm_6"]] * f[:, IDX["p_play"]]
        elif self.kind == "lastseason":
            v = f[:, IDX["prev_ppm"]] * f[:, IDX["prev_apps"]]
        elif self.kind == "ppg":
            v = f[:, IDX["base_ppm"]]
        elif self.kind == "market":
            v = f[:, IDX["price"]] + 2.0 * f[:, IDX["owned"]]
        else:
            raise ValueError(self.kind)
        m = float(np.mean(v))
        return v * (scale / m if m > 1e-9 else 1.0)


from .features import FEATURE_NAMES     # noqa: E402
IDX = {n: i for i, n in enumerate(FEATURE_NAMES)}

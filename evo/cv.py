"""Cross-validation over seasons, and the early-stopping rule.

Two protocols, both leave whole SEASONS out, because the unit that has to
generalise is a season: within one season the same players, clubs and
scoring quirks recur every week, so a random split of gameweeks would
leak almost everything.

  forward  the default. Expanding window: each scored season is
           validated by a model trained on every season BEFORE it, so
           the direction of time matches how the model will actually be
           used. With 2021/22 training-only that is three folds -
           2023/24, 2024/25 and 2025/26 - fewer than five, and the right
           ones.
  loso     leave-one-season-out. Each scored season is validated by a
           model trained on all the others, later ones included. More
           data per fold, but it is not the forward test.

Neither scores a season in cfg.train_only_seasons. 2021/22 has no xG,
no starts, no defensive contribution and no prior season - five of its
features are constants - and its baseline manager wins 0.11 of
six-manager leagues, so scoring against it is a different experiment
from the others and it was the one carrying the reported mean. It is
still trained on wherever it precedes the validation season.

In every fold the feature standardizer is fitted on the training seasons
alone, the hall of fame and the population never see the held-out season,
and validation is the paired comparison in evaluate.py against heuristic
managers - none of which is in the population, so a fold cannot be won by
learning the quirks of this generation's opponents.

The validation curve is recorded every cfg.valid_every generations. The
generation count that maximises MEAN validation score across folds is the
one the final model - trained on every season - is then run for. That is
the only thing the held-out seasons are allowed to decide. Run each fold
at several seeds (evo/slurm/cv.sbatch does) and summarize() averages the
seeds within a fold before it averages the folds, so the spread it
reports is between seasons and not between lucky draws.
"""
import json
import os
import re
import numpy as np

from .config import Config, SEASONS
from .features import load_seasons
from .sim import SeasonView
from .evaluate import paired_leagues, head_to_head
from .evolve import Evolver


def folds(kind="forward", seasons=None, train_only=None):
    """The folds of a protocol: dicts of name, train seasons, valid season.

    train_only defaults to Config().train_only_seasons. A forward fold
    needs at least two seasons before it, so that the standardizer and
    the priors are fitted on more than one.
    """
    seasons = list(seasons or SEASONS)
    if train_only is None:
        train_only = Config().train_only_seasons
    scored = [s for s in seasons if s not in set(train_only)]
    out = []
    if kind == "forward":
        for s in scored:
            tr = seasons[:seasons.index(s)]
            if len(tr) >= 2:
                out.append(dict(name=f"forward_{s}", train=tr, valid=[s]))
        return out
    for s in scored:
        tr = [x for x in seasons if x != s]
        out.append(dict(name=f"loso_{s}", train=tr, valid=[s]))
    return out


def train_fold(cfg, fold, out_dir, generations=None, valid_every=5,
               valid_leagues=40, resume=False, verbose=True):
    """Train on the fold's seasons, scoring the held-out one as we go."""
    generations = generations or cfg.generations
    os.makedirs(out_dir, exist_ok=True)
    cfg = Config(**{**cfg.to_dict(),
                    "train_seasons": tuple(fold["train"]),
                    "valid_seasons": tuple(fold["valid"])})
    ev = Evolver(cfg, out_dir, seasons=fold["train"])

    va = load_seasons(cfg, fold["valid"])
    vviews = {s: SeasonView(s, va[s], ev.std) for s in fold["valid"]}
    specs = paired_leagues(cfg, valid_leagues, fold["valid"], seed=99991)
    # the same paired measurement on TRAINING seasons, so the difference
    # between the two is a generalisation gap and not two different units
    tspecs = paired_leagues(cfg, valid_leagues, fold["train"], seed=99992)

    ck = os.path.join(out_dir, "ckpt.npz")
    if resume and os.path.exists(ck):
        ev.load(ck)
        if verbose:
            print(f"  resumed {fold['name']} at generation {ev.gen}")

    curve = []
    cp = os.path.join(out_dir, "curve.json")
    if resume and os.path.exists(cp):
        curve = json.load(open(cp))
    while ev.gen < generations:
        rec = ev.step()
        if ev.gen % valid_every == 0 or ev.gen == generations:
            v = head_to_head(ev.best, cfg, vviews, specs)
            t = head_to_head(ev.best, cfg, ev.views, tspecs)
            rec = dict(rec, valid=v, train_paired=t)
            curve.append(dict(gen=ev.gen, train=rec["best"],
                              train_mean=rec["mean"],
                              t_score=t["score"], t_se=t["se_points"],
                              gap=t["score"] - v["score"],
                              **{f"v_{k}": val for k, val in v.items()}))
            np.savez(os.path.join(out_dir, f"gen{ev.gen:05d}.npz"),
                     best=ev.best, mean=ev.std.mean, sd=ev.std.sd)
            with open(cp, "w") as fh:
                json.dump(curve, fh, indent=1)
            if verbose:
                print(f"  [{fold['name']}] gen {ev.gen:4d} "
                      f"fit {rec['mean']:.3f}  "
                      f"train {t['score']:+7.1f}+-{t['se_points']:.0f}  "
                      f"valid {v['score']:+7.1f}+-{v['se_points']:.0f}  "
                      f"gap {t['score'] - v['score']:+7.1f}  "
                      f"win {v['win']:.2f} vs {v['ref_win']:.2f}",
                      flush=True)
        ev.save()
    ev.close()
    with open(os.path.join(out_dir, "fold.json"), "w") as fh:
        json.dump(dict(fold=fold, cfg=cfg.to_dict(), curve=curve), fh, indent=1)
    return curve


SMOOTH = 3


def _fold_of(rel):
    """The fold a run directory belongs to, with the seed stripped: both
    `seed1001/forward_2025-26` and `forward_2025-26_s1001` are runs of the
    fold `forward_2025-26`."""
    parts = [p for p in rel.split(os.sep) if not re.fullmatch(r"seed\d+", p)]
    name = parts[-1] if parts else rel
    return re.sub(r"_s\d+$", "", name)


def summarize(root, key="v_score", smooth=SMOOTH):
    """Mean validation curve across folds, and the generation to stop at.

    Every curve.json under root is a run; runs of the same fold at
    different seeds are averaged first, so that a fold with three seeds
    counts once and the spread reported is between seasons.

    The stopping generation is read off a moving average of the curve, not
    off its raw argmax. A draft league is noisy enough that the single
    best checkpoint is usually the luckiest one rather than the best one,
    and picking it is overfitting the validation seasons - the one thing
    cross-validation exists to prevent.
    """
    runs = {}
    for dp, _, fn in os.walk(root):
        if "curve.json" in fn:
            rel = os.path.relpath(dp, root)
            runs.setdefault(_fold_of(rel), []).append(
                json.load(open(os.path.join(dp, "curve.json"))))
    if not runs:
        return None
    fold_curves = dict(sorted(runs.items()))
    gens = sorted(set.intersection(*[{r["gen"] for r in c}
                                     for cs in fold_curves.values()
                                     for c in cs]))

    def col(g, name, default=float("nan")):
        # one number per fold: the mean over that fold's seeds
        out = []
        for cs in fold_curves.values():
            v = [next((r.get(name, default) for r in c if r["gen"] == g),
                      default) for c in cs]
            out.append(float(np.nanmean(v)) if len(v) else default)
        return out

    mean = []
    for g in gens:
        vals = col(g, key)
        se = col(g, "v_se_points")
        mean.append(dict(
            gen=g, valid=float(np.mean(vals)),
            # spread across folds, and the paired noise floor within them
            valid_se=(float(np.std(vals, ddof=1) / np.sqrt(len(vals)))
                      if len(vals) > 1 else float("nan")),
            paired_se=float(np.nanmean(se) / np.sqrt(len(se))),
            train=float(np.nanmean(col(g, "t_score"))),
            fit=float(np.mean(col(g, "train_mean")))))
        mean[-1]["gap"] = mean[-1]["train"] - mean[-1]["valid"]
    v = np.array([r["valid"] for r in mean])
    k = max(1, min(smooth, len(v)))
    ker = np.ones(k)
    sm = np.convolve(v, ker, "same") / np.convolve(np.ones_like(v), ker, "same")
    for r, x in zip(mean, sm):
        r["valid_smooth"] = float(x)
    i = int(np.argmax(sm))
    # the peak is a max over noisy checkpoints and is biased upward by
    # exactly that; the mean over checkpoints is the number to believe
    return dict(folds=list(fold_curves),
                seeds={f: len(cs) for f, cs in fold_curves.items()},
                curve=mean, smooth=k,
                mean_valid=float(np.mean(v)),
                mean_valid_se=float(np.std(v, ddof=1) / np.sqrt(len(v)))
                if len(v) > 1 else float("nan"),
                best_gen=mean[i]["gen"], best_valid=float(sm[i]),
                best_valid_raw=mean[i]["valid"])

"""Command line for the evolutionary draft player.

    python -m evo.run features [--rebuild]
    python -m evo.run selftest
    python -m evo.run train  --out runs/final [--generations N] [--all-seasons]
    python -m evo.run cv     --out runs/cv [--kind loso|forward] [--fold i]
    python -m evo.run report --out runs/cv
    python -m evo.run live   --model runs/final/ckpt.npz [--offline]

Every subcommand takes the Config fields as --flags (pop-size,
generations, workers, ...), so a SLURM script can sweep without editing
the source.
"""
import argparse
import json
import os
import sys
import numpy as np

from .config import Config, SEASONS
from . import cv as cvmod


def add_config_args(ap):
    d = Config()
    for k, v in d.to_dict().items():
        if k in ("train_seasons", "valid_seasons"):
            ap.add_argument("--" + k.replace("_", "-"), nargs="+", default=None)
        elif isinstance(v, bool):
            ap.add_argument("--" + k.replace("_", "-"),
                            action=argparse.BooleanOptionalAction, default=None)
        else:
            ap.add_argument("--" + k.replace("_", "-"), type=type(v),
                            default=None)
    return ap


def cfg_from_args(a):
    base = Config().to_dict()
    for k in list(base):
        v = getattr(a, k, None)
        if v is not None:
            base[k] = tuple(v) if k.endswith("_seasons") else v
    base["train_seasons"] = tuple(base["train_seasons"])
    base["valid_seasons"] = tuple(base["valid_seasons"])
    return Config(**base)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="evo.run")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = add_config_args(sub.add_parser("features"))
    p.add_argument("--rebuild", action="store_true")

    add_config_args(sub.add_parser("selftest"))

    p = add_config_args(sub.add_parser("train"))
    p.add_argument("--out", required=True)
    p.add_argument("--all-seasons", action="store_true",
                   help="train on every season (the live model)")
    p.add_argument("--resume", action="store_true")

    p = add_config_args(sub.add_parser("cv"))
    p.add_argument("--out", required=True)
    p.add_argument("--kind", default="loso", choices=("loso", "forward"))
    p.add_argument("--fold", type=int, default=-1,
                   help="fold index for a SLURM array; -1 runs them all")
    p.add_argument("--valid-every", type=int, default=5)
    p.add_argument("--valid-leagues", type=int, default=40)
    p.add_argument("--resume", action="store_true")

    p = sub.add_parser("report")
    p.add_argument("--out", required=True)

    p = add_config_args(sub.add_parser("live"))
    p.add_argument("--model", required=True)
    p.add_argument("--offline", action="store_true")
    p.add_argument("--gw", type=int, default=None)
    p.add_argument("--json", default="data/evo_plan.json")

    a = ap.parse_args(argv)

    if a.cmd == "features":
        from .features import load_seasons
        load_seasons(cfg_from_args(a), rebuild=a.rebuild, verbose=True)
        return 0

    if a.cmd == "selftest":
        from .selftest import run_all
        return 0 if run_all(cfg_from_args(a)) else 1

    if a.cmd == "train":
        from .evolve import Evolver
        cfg = cfg_from_args(a)
        seasons = list(SEASONS) if a.all_seasons else list(cfg.train_seasons)
        ev = Evolver(cfg, a.out, seasons=seasons)
        ck = os.path.join(a.out, "ckpt.npz")
        if a.resume and os.path.exists(ck):
            ev.load(ck)
            print(f"resumed at generation {ev.gen}", flush=True)
        while ev.gen < cfg.generations:
            rec = ev.step()
            ev.save()
            print(f"gen {rec['gen']:4d} mean {rec['mean']:.3f} "
                  f"best {rec['best']:.3f} sigma {rec['sigma']:.3f} "
                  f"{rec['secs']}s", flush=True)
        ev.close()
        print("wrote", ck)
        return 0

    if a.cmd == "cv":
        cfg = cfg_from_args(a)
        fs = cvmod.folds(a.kind)
        idx = range(len(fs)) if a.fold < 0 else [a.fold]
        for i in idx:
            f = fs[i]
            print(f"=== fold {i}: {f['name']}  train={f['train']} "
                  f"valid={f['valid']}", flush=True)
            cvmod.train_fold(cfg, f, os.path.join(a.out, f["name"]),
                             generations=cfg.generations,
                             valid_every=a.valid_every,
                             valid_leagues=a.valid_leagues,
                             resume=a.resume)
        return 0

    if a.cmd == "report":
        s = cvmod.summarize(a.out)
        if not s:
            print("no folds with a curve under", a.out)
            return 1
        print(f"folds: {', '.join(s['folds'])}")
        print("paired points per season against the baseline manager, "
              "meaned over folds")
        print(f"{'gen':>6} {'fitness':>8} {'train':>8} {'valid':>8} "
              f"{'+-fold':>7} {'+-pair':>7} {'gap':>7} {'smoothed':>9}")
        for r in s["curve"]:
            print(f"{r['gen']:6d} {r['fit']:8.3f} {r['train']:+8.1f} "
                  f"{r['valid']:+8.1f} {r['valid_se']:7.1f} "
                  f"{r['paired_se']:7.1f} {r['gap']:+7.1f} "
                  f"{r['valid_smooth']:+9.1f}")
        print(f"\nstop at generation {s['best_gen']}: "
              f"{s['best_valid']:+.1f} points a season over the baseline "
              f"({s['smooth']}-point moving average; raw "
              f"{s['best_valid_raw']:+.1f})")
        print("train it on every season with:\n"
              f"  python -m evo.run train --out evo/runs/final "
              f"--all-seasons --generations {s['best_gen']}")
        with open(os.path.join(a.out, "summary.json"), "w") as fh:
            json.dump(s, fh, indent=1)
        return 0

    if a.cmd == "live":
        from .live import main as live_main
        return live_main(cfg_from_args(a), a.model, offline=a.offline,
                         gw=a.gw, out_json=a.json)

    return 1


if __name__ == "__main__":
    sys.exit(main())

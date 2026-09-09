# Running the draft player on SLURM

Everything below is copy-paste. Two lines in `evo/slurm/env.sh` are the
only site-specific edit.

## 0. What you need on the cluster

Python 3.10+ with numpy, pandas and scipy. Nothing else; no GPU.

```
unzip schwaddy-fpl-evo.zip && cd schwaddy-fpl
python3 -c "import numpy, pandas, scipy; print('ok')"    # if this fails:
pip install --user -r requirements.txt                     # or your module load
```

## 1. Edit env.sh - the one site-specific step

```
nano evo/slurm/env.sh
```

Uncomment and fix the two lines at the top for your site, e.g.

```
module load python/3.11
source "$HOME/venvs/fpl/bin/activate"
```

Leave the rest. If your cluster needs `--account` or `--partition`,
add them to the `#SBATCH` header of each of the three `.sbatch` files,
or pass them on the command line: `sbatch -A myaccount -p compute ...`.

## 2. Feature cache and self-tests (one job, ~5 minutes)

```
sbatch evo/slurm/features.sbatch
```

Note the job id it prints. `logs/features-<id>.out` should end with
`all checks passed`. If it does not, stop and send me the log.

## 3. Cross-validation (five array tasks, up to 8 hours each)

```
sbatch --dependency=afterok:<features job id> evo/slurm/cv.sbatch
```

Watch with `squeue -u $USER`. Each task is one held-out season and
checkpoints every generation; a task that is pre-empted or times out is
requeued with `scontrol requeue <jobid>_<task>` and carries on where it
stopped. When all five have finished:

```
python -m evo.run report --out evo/runs/cv
```

Read two things off it: `mean over all checkpoints` (the number to
believe) and `stop at generation N`.

## 4. The final model (one job, ~1 hour)

```
GENS=<N from step 3> sbatch evo/slurm/train.sbatch
```

Writes `evo/runs/final/ckpt.npz`. That single file (~100 KB) is the
model.

## 5. Bring it home and use it before the deadline

On your own machine, in a clone at the SAME commit the cluster ran
(`git rev-parse HEAD` there; `git checkout <that>` here):

```
scp <cluster>:<path>/schwaddy-fpl/evo/runs/final/ckpt.npz evo/runs/final/
python -m evo.run live --model evo/runs/final/ckpt.npz
```

That reads the draft API, brings the injury log up to today, works out
whether the week is in the waiver window or free agency, and writes
`data/evo_plan.json` - the eleven, the bench order and the ranked moves.
It prints the same.

## If something goes wrong

- `cannot reshape` / "this genome has N weights": the checkpoint and the
  code are from different commits. Check out the cluster's commit.
- `no eligible player`: the feature cache is stale; rerun step 2 with
  `--rebuild` added to the `features` line in `features.sbatch`.
- A fold's `curve.json` missing: that array task did not finish; requeue
  it.

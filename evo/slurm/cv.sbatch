#!/bin/bash
# Forward cross-validation: one array task per (fold, seed).
# Submit AFTER features.sbatch has finished:
#   sbatch --dependency=afterok:<featuresjob> evo/slurm/cv.sbatch
#
# Three forward folds (2023/24, 2024/25, 2025/26; 2021/22 is training-
# only) at three seeds each. A single seed of a draft league is mostly
# the draw it got: the seeds are averaged within a fold by
# `python -m evo.run report`, and the spread it prints is between
# seasons. To change either count edit the two numbers below and the
# --array range to FOLDS*SEEDS-1.
#SBATCH --job-name=fpl-cv
#SBATCH --output=logs/cv-%A_%a.out
#SBATCH --array=0-8
#SBATCH --cpus-per-task=32
#SBATCH --mem=16G
#SBATCH --time=12:00:00

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-.}"
source evo/slurm/env.sh

OUT=${OUT:-evo/runs/cv}
FOLDS=${FOLDS:-3}
SEEDS=${SEEDS:-3}
FOLD=$((SLURM_ARRAY_TASK_ID % FOLDS))
SEED=$((1000 + SLURM_ARRAY_TASK_ID / FOLDS))

# --resume picks up ckpt.npz, so a pre-empted or timed-out task can be
# requeued and will carry on from the generation it reached.
python -m evo.run cv \
  --out "$OUT/seed$SEED" \
  --kind forward \
  --fold "$FOLD" \
  --pop-size 200 \
  --leagues-per-genome 24 \
  --generations 200 \
  --valid-every 5 \
  --valid-leagues 120 \
  --workers "${SLURM_CPUS_PER_TASK}" \
  --seed "$SEED" \
  --resume

# when the last task finishes, print the aggregate curve
python -m evo.run report --out "$OUT" || true

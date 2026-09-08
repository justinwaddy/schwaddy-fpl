#!/bin/bash
# Leave-one-season-out cross-validation: one fold per array task.
# Submit AFTER features.sbatch has finished:
#   sbatch --dependency=afterok:<featuresjob> evo/slurm/cv.sbatch
#SBATCH --job-name=fpl-cv
#SBATCH --output=logs/cv-%A_%a.out
#SBATCH --array=0-4
#SBATCH --cpus-per-task=32
#SBATCH --mem=16G
#SBATCH --time=08:00:00

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-.}"
source evo/slurm/env.sh

OUT=${OUT:-evo/runs/cv}

# --resume picks up ckpt.npz, so a pre-empted or timed-out task can be
# requeued and will carry on from the generation it reached.
python -m evo.run cv \
  --out "$OUT" \
  --kind loso \
  --fold "${SLURM_ARRAY_TASK_ID}" \
  --pop-size 200 \
  --leagues-per-genome 8 \
  --generations 200 \
  --valid-every 5 \
  --valid-leagues 120 \
  --workers "${SLURM_CPUS_PER_TASK}" \
  --seed $((1000 + SLURM_ARRAY_TASK_ID)) \
  --resume

# when the last fold finishes, print the aggregate curve
python -m evo.run report --out "$OUT" || true

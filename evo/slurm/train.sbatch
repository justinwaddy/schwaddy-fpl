#!/bin/bash
# The live model: every season, run for the generation count that
# cross-validation chose. Read GENS off `python -m evo.run report`.
#SBATCH --job-name=fpl-train
#SBATCH --output=logs/train-%j.out
#SBATCH --cpus-per-task=32
#SBATCH --mem=16G
#SBATCH --time=12:00:00

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-.}"
source evo/slurm/env.sh

OUT=${OUT:-evo/runs/final}
GENS=${GENS:?set GENS to the best generation from: python -m evo.run report --out evo/runs/cv}

python -m evo.run train \
  --out "$OUT" \
  --all-seasons \
  --generations "$GENS" \
  --pop-size 200 \
  --leagues-per-genome 8 \
  --workers "${SLURM_CPUS_PER_TASK}" \
  --resume

echo "model: $OUT/ckpt.npz"
python -m evo.run live --model "$OUT/ckpt.npz" --offline \
  --json evo/runs/evo_plan.json

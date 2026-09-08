#!/bin/bash
# Build the point-in-time feature cache once, so that the training array
# does not have five tasks racing to write the same npz files.
#SBATCH --job-name=fpl-features
#SBATCH --output=logs/features-%j.out
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=00:20:00

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-.}"
source evo/slurm/env.sh

python -m evo.run features
python -m evo.run selftest

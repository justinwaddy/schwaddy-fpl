# Cluster environment. Edit these two lines for your site and nothing else
# in evo/ should need touching.
#
# The package needs only numpy, pandas and scipy - the same three
# requirements.txt already pins - so a bare venv is enough; there is no
# GPU path and nothing to compile.

# module load python/3.11                      # e.g. on a Lmod cluster
# source "$HOME/venvs/fpl/bin/activate"

export PYTHONUNBUFFERED=1
# each worker process is single-threaded on purpose: the parallelism is
# over leagues, and letting BLAS also fan out oversubscribes the node
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1

mkdir -p logs

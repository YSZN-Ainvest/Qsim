#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Fail if NAS is unavailable; keep all generated caches and temporary files there.
qsim_artifacts=/mnt/nas/qinml_cache/qsim
test -d /mnt/nas/qinml_cache && test -w /mnt/nas/qinml_cache || {
  echo 'NAS cache root is unavailable or not writable' >&2
  exit 1
}
mkdir -p "$qsim_artifacts"/{cache/matplotlib,tmp,logs,result}
export TMPDIR="$qsim_artifacts/tmp" XDG_CACHE_HOME="$qsim_artifacts/cache"
export SQLITE_TMPDIR="$qsim_artifacts/tmp" QSIM_CACHE_ROOT="$qsim_artifacts/cache"
export PIP_CACHE_DIR="$qsim_artifacts/cache/pip"
export PYTHONPYCACHEPREFIX="$qsim_artifacts/cache/pycache"
export MPLCONFIGDIR="$qsim_artifacts/cache/matplotlib"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 ARROW_NUM_THREADS=1 ARROW_IO_THREADS=1
if [[ -n "${QSIM_PYTHON:-}" ]]; then
  qsim_python="$QSIM_PYTHON"
elif [[ -x /home/qinml/miniconda3/envs/alpha/bin/python ]]; then
  qsim_python=/home/qinml/miniconda3/envs/alpha/bin/python
else
  qsim_python=python3
fi
exec "$qsim_python" -m qsim --config "${1:-configs/daily_demo.toml}" "${@:2}"

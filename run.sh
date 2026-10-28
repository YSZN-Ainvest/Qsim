#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -n "${QSIM_PYTHON:-}" ]]; then
  qsim_python="$QSIM_PYTHON"
elif [[ -x /home/qinml/miniconda3/envs/alpha/bin/python ]]; then
  qsim_python=/home/qinml/miniconda3/envs/alpha/bin/python
else
  qsim_python=python3
fi
exec "$qsim_python" -m qsim --config "${1:-configs/daily_demo.toml}" "${@:2}"

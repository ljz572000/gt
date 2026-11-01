#!/bin/zsh
set -e
cd "$(dirname "$0")"

if [[ -x ".venv/bin/python" ]]; then
  exec ".venv/bin/python" gt_studio.py
fi

exec python3 gt_studio.py

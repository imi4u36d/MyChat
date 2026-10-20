#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Prefer a project-local virtualenv, then the companion export tool's venv.
if [ -x .venv/bin/python ]; then
  PY=".venv/bin/python"
elif [ -n "${WECHAT_TOOL:-}" ] && [ -x "${WECHAT_TOOL}/venv/bin/python" ]; then
  PY="${WECHAT_TOOL}/venv/bin/python"
elif [ -x "${HOME}/wechat-export-tool/venv/bin/python" ]; then
  PY="${HOME}/wechat-export-tool/venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi

exec "$PY" app.py

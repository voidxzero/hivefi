#!/bin/sh
cd "$(dirname "$0")" || exit 1
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" -m hivefi "$@"

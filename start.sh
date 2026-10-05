#!/usr/bin/env bash
# Runs the bot inside its own virtual environment (.venv); see start.ps1.
set -e
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt
if [ ! -f .env ]; then cp .env.example .env; echo "Fill in .env then run again."; exit 1; fi
set -a; . ./.env; set +a
exec .venv/bin/python run_polling.py

#!/usr/bin/env bash
# One command: install into a fresh venv if needed, run the tests, then a live verify of the example run.
set -u
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { python3 -m venv .venv && .venv/bin/pip install -q -e '.[test]'; } || exit 2
.venv/bin/python -m pytest -q tests || exit 1
.venv/bin/python -m receipt_scraper verify examples/out || exit 1
echo "check: all green"

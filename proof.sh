#!/bin/sh
# One-screen proof: unit tests, offline evals, seeded fault injection. No API key needed.
set -e
cd "$(dirname "$0")"
PY=.venv/bin/python
echo "== tests";  $PY -m pytest -q 2>&1 | tail -1
echo "== evals";  $PY -m errand.evals | tail -1
echo "== fuzz";   $PY -m errand.fuzz --runs 500 --seed 1 | tail -1
echo "== bug log: see BUGS.md; generated reports: EVAL_TABLE.md, FUZZ_REPORT.md"

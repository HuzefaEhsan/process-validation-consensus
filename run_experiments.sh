#!/usr/bin/env bash
# run_experiments.sh -- one command reproducing every reported number (offline, no LLM in the loop).
#   bash run_experiments.sh                 -> data check, tests, experiments and audit into results/
#   bash run_experiments.sh --verify-repro  -> additionally reruns into a temporary directory and
#                                              compares the two runs file by file
set -euo pipefail
cd "$(dirname "$0")"

echo "== 0. dependencies (pip install -r requirements.txt if missing)"
python3 -c "import rfc8785, mesa, pytest, scipy" 2>/dev/null || python3 -m pip install -r requirements.txt

echo "== 1. dataset (GSM8K test split, pinned upstream commit; SHA-256 as recorded in results/results.json)"
python3 scripts/download_gsm8k.py

echo "== 2. audited modules unmodified"
if command -v sha256sum >/dev/null 2>&1; then
  (cd src && sha256sum -c AUDITED_SHA256.txt)
else
  (cd src && shasum -a 256 -c AUDITED_SHA256.txt)
fi

echo "== 3. full test suite"
python3 -m pytest -q

echo "== 4. experiments -> results/"
# audit/audit_corrections.py moves the raw outputs to generated_* only if no generated_* file exists,
# so leftovers from an earlier run would be reused; clear them so every run is self-contained.
rm -f results/generated_*
python3 src/experiments.py --out results

echo "== 5. verify every number in the generated RESULTS_SUMMARY.md against the generated results.json"
python3 audit/verify_summary.py results

echo "== 6. audit: independent checks, corrected results.json + RESULTS_SUMMARY.md, verification"
python3 audit/independent_checks.py results > /dev/null
python3 audit/audit_corrections.py results
python3 audit/verify_corrected.py results

if [ "${1:-}" = "--verify-repro" ]; then
  echo "== 7. second run + reproducibility check"
  TMP=$(mktemp -d)
  python3 src/experiments.py --out "$TMP/results" > "$TMP/run2.log"
  python3 audit/verify_summary.py "$TMP/results" > /dev/null
  python3 audit/independent_checks.py "$TMP/results" > /dev/null
  python3 audit/audit_corrections.py "$TMP/results" > /dev/null
  python3 audit/verify_corrected.py "$TMP/results" > /dev/null
  python3 audit/check_reproducibility.py results "$TMP/results"
  echo "second run kept in $TMP"
fi

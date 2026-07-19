#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLOPT_ROOT="${SKILLOPT_ROOT:-/Users/pingxia/projects/SkillOpt}"
RUNNER="$SKILLOPT_ROOT/outputs/skill_probe_template/run_static_benchmark.py"
PYTHON_BIN="$SKILLOPT_ROOT/.venv/bin/python"
REPORT_PATH="${TMPDIR:-/tmp}/deer-flow-nextos-skillopt-report.json"

if [[ ! -x "$PYTHON_BIN" || ! -f "$RUNNER" ]]; then
  echo "SkillOpt runtime not found under $SKILLOPT_ROOT" >&2
  exit 1
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" "$RUNNER" \
  --config skillopt/nextos-commander/config.json \
  --tasks skillopt/nextos-commander/tasks.json \
  --out "$REPORT_PATH" >/dev/null

"$PYTHON_BIN" -c '
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
scores = report["baseline"]
failed = [name for name, row in scores.items() if row["hard"] != 1.0]
print(json.dumps({"status": "fail" if failed else "pass", "scores": scores}, indent=2))
raise SystemExit(1 if failed else 0)
' "$REPORT_PATH"

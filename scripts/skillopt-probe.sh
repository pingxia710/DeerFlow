#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLOPT_ROOT="${SKILLOPT_ROOT:-/Users/pingxia/projects/SkillOpt}"
RUNNER="$SKILLOPT_ROOT/outputs/skill_probe_template/run_static_benchmark.py"
PYTHON_BIN="$SKILLOPT_ROOT/.venv/bin/python"
REPORT_DIR="${TMPDIR:-/tmp}/deer-flow-nextos-skillopt-reports"

if [[ ! -x "$PYTHON_BIN" || ! -f "$RUNNER" ]]; then
  echo "SkillOpt runtime not found under $SKILLOPT_ROOT" >&2
  exit 1
fi

cd "$ROOT_DIR"
mkdir -p "$REPORT_DIR"

CONFIG_PATHS=(
  skillopt/nextos-commander/config.json
  skillopt/command-room-auditors/runtime-reliability.json
  skillopt/command-room-auditors/persistence-migration.json
  skillopt/command-room-auditors/frontend-protocol.json
  skillopt/command-room-auditors/security.json
  skillopt/command-room-auditors/platform-ops.json
)
TASK_PATHS=(
  skillopt/nextos-commander/tasks.json
  skillopt/command-room-auditors/tasks.json
  skillopt/command-room-auditors/tasks.json
  skillopt/command-room-auditors/tasks.json
  skillopt/command-room-auditors/tasks.json
  skillopt/command-room-auditors/tasks.json
)
REPORT_PATHS=(
  "$REPORT_DIR/nextos-commander.json"
  "$REPORT_DIR/runtime-reliability.json"
  "$REPORT_DIR/persistence-migration.json"
  "$REPORT_DIR/frontend-protocol.json"
  "$REPORT_DIR/security.json"
  "$REPORT_DIR/platform-ops.json"
)

for index in "${!CONFIG_PATHS[@]}"; do
  "$PYTHON_BIN" "$RUNNER" \
    --config "${CONFIG_PATHS[$index]}" \
    --tasks "${TASK_PATHS[$index]}" \
    --out "${REPORT_PATHS[$index]}" >/dev/null
done

"$PYTHON_BIN" - "${REPORT_PATHS[@]}" <<'PY'
import json
import sys
from pathlib import Path

packs = {}
failed = []
for raw_path in sys.argv[1:]:
    path = Path(raw_path)
    report = json.loads(path.read_text(encoding="utf-8"))
    scores = report["baseline"]
    packs[path.stem] = scores
    if any(row["hard"] != 1.0 for row in scores.values()):
        failed.append(path.stem)

print(json.dumps({"status": "fail" if failed else "pass", "packs": packs}, indent=2))
raise SystemExit(1 if failed else 0)
PY

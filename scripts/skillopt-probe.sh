#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLOPT_ROOT="${SKILLOPT_ROOT:-/Users/pingxia/projects/SkillOpt}"
CONFIG="$REPO_ROOT/docs/skillopt/naxus-round/config.json"
TASKS="$REPO_ROOT/docs/skillopt/naxus-round/tasks.json"
OUT="$REPO_ROOT/docs/skillopt/naxus-round/static_report.json"
SKILL="$REPO_ROOT/skills/custom/naxus-round/SKILL.md"

if [[ ! -f "$SKILL" ]]; then
  echo "Missing local skill: $SKILL" >&2
  echo "Create or restore skills/custom/naxus-round/SKILL.md before running this probe." >&2
  exit 2
fi

if [[ ! -d "$SKILLOPT_ROOT" ]]; then
  echo "Missing SkillOpt project: $SKILLOPT_ROOT" >&2
  exit 2
fi

cd "$SKILLOPT_ROOT"
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

python outputs/skill_probe_template/run_static_benchmark.py \
  --config "$CONFIG" \
  --tasks "$TASKS" \
  --out "$OUT"

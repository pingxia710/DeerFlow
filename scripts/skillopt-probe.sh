#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLOPT_ROOT="${SKILLOPT_ROOT:-/Users/pingxia/projects/SkillOpt}"
CONFIG="$REPO_ROOT/docs/skillopt/naxus-round/config.json"
TASKS="$REPO_ROOT/docs/skillopt/naxus-round/tasks.json"
OUT="$REPO_ROOT/docs/skillopt/naxus-round/static_report.json"
SKILL="$REPO_ROOT/skills/custom/naxus-round/SKILL.md"
BEHAVIOR_OUT="$REPO_ROOT/docs/skillopt/naxus-round/behavior_report.json"
BEHAVIOR_SCHEMA="$REPO_ROOT/docs/skillopt/naxus-round/behavior_schema.json"
BEHAVIOR_PROBE="$REPO_ROOT/scripts/command-room-skill-behavior-probe.py"

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

# The static benchmark resolves a relative skill_path from its current directory.
# Keep the checked-in config portable by invoking it from this worktree.
cd "$REPO_ROOT"
python "$SKILLOPT_ROOT/outputs/skill_probe_template/run_static_benchmark.py" \
  --config "$CONFIG" \
  --tasks "$TASKS" \
  --out "$OUT"

if [[ "${SKILLOPT_STATIC_ONLY:-0}" == "1" ]]; then
  echo "SKILLOPT_STATIC_ONLY=1: skipped model-backed behavioral gate." >&2
  exit 0
fi

behavior_args=(
  --skill "$SKILL"
  --schema "$BEHAVIOR_SCHEMA"
  --out "$BEHAVIOR_OUT"
)
if [[ -n "${SKILLOPT_MODEL:-}" ]]; then
  behavior_args+=(--model "$SKILLOPT_MODEL")
fi
if [[ -n "${SKILLOPT_RESPONSES:-}" ]]; then
  behavior_args+=(--responses "$SKILLOPT_RESPONSES")
fi
if [[ "${SKILLOPT_DRY_RUN:-0}" == "1" ]]; then
  behavior_args+=(--dry-run)
fi

python "$BEHAVIOR_PROBE" "${behavior_args[@]}"

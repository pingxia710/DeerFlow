# Naxus Round SkillOpt Probe

SkillOpt gate for the local `skills/custom/naxus-round/SKILL.md`.

Run from the DeerFlow repo root:

```bash
bash scripts/skillopt-probe.sh
```

The entrypoint first checks static rule coverage, then runs a read-only,
model-backed behavioral rollout for three high-signal decisions: stop after
strong implementation evidence, use the smallest path for a small fact, and
request confirmation at the bottom boundary. The behavior gate writes
`behavior_report.json` and fails the command on semantic drift.

Use `SKILLOPT_STATIC_ONLY=1` only when model execution is intentionally
unavailable. `SKILLOPT_DRY_RUN=1` validates behavioral probe setup without a
model call. This probe follows the WorkOS lesson: keep skills small,
failure-driven, and evidence-backed instead of turning them into broad project
encyclopedias.

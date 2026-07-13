# NextOS Commander SkillOpt Probe

SkillOpt gate for the local `skills/custom/nextos-commander/SKILL.md`.

Run from the NextOS repo root:

```bash
bash scripts/skillopt-probe.sh
```

The entrypoint first checks static rule coverage, then runs a read-only,
model-backed behavioral rollout for three high-signal decisions: require a
different checking AI and independent opposition before relying on worker
output, delegate even a small fact out of the lead context, and request
confirmation at the bottom boundary. The behavior gate writes
`behavior_report.json` and fails the command on semantic drift.

Use `SKILLOPT_STATIC_ONLY=1` only when model execution is intentionally
unavailable. `SKILLOPT_DRY_RUN=1` validates behavioral probe setup without a
model call. This probe follows the WorkOS lesson: keep skills small,
failure-driven, and evidence-backed instead of turning them into broad project
encyclopedias.

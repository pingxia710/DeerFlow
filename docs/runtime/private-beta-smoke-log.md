# Private Beta Smoke Log

Use this file for the 1-2 day real-use smoke from
`docs/runtime/private-beta-runbook.md`.

| Date       | Operator | Commit     | Config Summary                                                  | Flow        | Result  | Evidence                                                |
| ---------- | -------- | ---------- | --------------------------------------------------------------- | ----------- | ------- | ------------------------------------------------------- |
| 2026-07-05 | pending  | `9e3316c1` | single worker, `run_events.backend=db` required for shared/prod | not started | pending | `docs/runtime/private-beta-seal-evidence-2026-07-05.md` |

## Notes

- Do not mark private beta observation complete until real sessions have run for
  1-2 days.
- File separate fixes for regressions; do not batch runtime changes into this
  observation log.

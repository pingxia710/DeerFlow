# Private Beta Smoke Log

Use this file for the 1-2 day real-use smoke from
`docs/runtime/private-beta-runbook.md`.

| Date       | Operator | Commit     | Config Summary                                                  | Flow        | Result  | Evidence                                                |
| ---------- | -------- | ---------- | --------------------------------------------------------------- | ----------- | ------- | ------------------------------------------------------- |
| 2026-07-05 | pending  | `9e3316c1` | single worker, `run_events.backend=db` required for shared/prod | not started | pending | `docs/runtime/private-beta-seal-evidence-2026-07-05.md` |
| 2026-07-05 | Codex    | `b6d7c1b3` | no service boot; preflight only                                 | preflight   | pass    | backend guard `25 passed`; frontend history `62 passed` |
| 2026-07-05 | Codex    | `05e76ab4` | existing local stack on `localhost:2026`; Gateway `:8001`; frontend `:6001`; port `3000` occupied by unrelated `hiveops-web` | API + UI empty-thread smoke | pass | `/health` 200; register/thread/search/runs/messages API smoke 200; browser loaded and reloaded `codex-ui-smoke-20260705T020246Z-250995` with no console warnings/errors |

## Notes

- Do not mark private beta observation complete until real sessions have run for
  1-2 days.
- File separate fixes for regressions; do not batch runtime changes into this
  observation log.
- This smoke intentionally avoided model execution; it validates auth cookies,
  nginx-to-Gateway routing, thread persistence, empty run/message recovery, and
  browser refresh on a persisted thread.
- Existing `logs/frontend.log` contained older duplicate React key warnings and
  one history timeout from prior manual sessions; the empty-thread browser smoke
  above did not reproduce them. Treat recurrence during the 1-2 day run as a
  separate fix ticket.

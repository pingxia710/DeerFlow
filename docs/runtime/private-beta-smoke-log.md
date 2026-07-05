# Private Beta Smoke Log

Use this file for the 1-2 day real-use smoke from
`docs/runtime/private-beta-runbook.md`.

| Date       | Operator | Commit     | Config Summary                                                  | Flow        | Result  | Evidence                                                |
| ---------- | -------- | ---------- | --------------------------------------------------------------- | ----------- | ------- | ------------------------------------------------------- |
| 2026-07-05 | pending  | `9e3316c1` | single worker, `run_events.backend=db` required for shared/prod | not started | pending | `docs/runtime/private-beta-seal-evidence-2026-07-05.md` |
| 2026-07-05 | Codex    | `b6d7c1b3` | no service boot; preflight only                                 | preflight   | pass    | backend guard `25 passed`; frontend history `62 passed` |
| 2026-07-05 | Codex    | `05e76ab4` | existing local stack on `localhost:2026`; Gateway `:8001`; frontend `:6001`; port `3000` occupied by unrelated `hiveops-web` | API + UI empty-thread smoke | pass | `/health` 200; register/thread/search/runs/messages API smoke 200; browser loaded and reloaded `codex-ui-smoke-20260705T020246Z-250995` with no console warnings/errors |
| 2026-07-05 | Codex    | `7b0317aa` | existing local stack on `localhost:2026`; real model run via UI | model run + refresh recovery | pass after fix | Found duplicate restored messages during `codex-run-smoke-20260705T020448Z-195602`; fixed in `7b0317aa`; retest `codex-dom-smoke-20260705T021600Z-373729` run `fe5773a5-9d29-4bdc-9e86-05aaec58b4f3` reached `success`, marker appeared once in user bubble and once in AI bubble before/after reload, duplicate-key warnings `0` |
| 2026-07-05 | Codex    | `14acaff2` | existing local stack on `localhost:2026` | cancel active run | pass | `codex-cancel-ui-20260705T022019Z-838205` run `3ed6ac9f-83f1-4db4-9bc1-d6ad277f3176`; cancel API returned `202`; terminal status `interrupted`; textarea enabled after reload; duplicate-key warnings `0`; console errors `0` |
| 2026-07-05 | Codex    | `14acaff2` | stack restarted with `scripts/serve.sh --dev --daemon --skip-install`; temp local `DEER_FLOW_CONFIG_PATH` used for trusted sandbox mount override, then removed | terminal run replay after restart | pass | Before restart: `codex-replay-smoke-20260705T022655Z-444948` run `70b631dc-3542-4a59-a68e-f5261b3bfa39` reached `success`; after stack restart and login, run API still `success`, messages API `3` rows, marker visible once in user bubble and once in AI bubble, duplicate-key warnings `0` |
| 2026-07-05 | Codex    | `6c6c934d` | existing local stack on `localhost:2026`; restored missing temp `DEER_FLOW_CONFIG_PATH` for this workstation's trusted local sandbox mount override | artifact provenance + download | pass | API run `codex-artifact-api-20260705023920-a85a1f` / `d1a8e6bc-019b-45b8-990d-394865600d8a` reached `success`; `/runs/{run_id}/artifacts` returned one runtime-observed `artifact.presented` entry from `present_files` with `available=true`, `display_policy=inline`, `mime_type=text/plain`, `size_bytes=49`, sha256 `d02f09b551b46118fcbd89c350c03927dff9adbc8c1e9471e9efcedd7cefa18e`; artifact download returned `200`, `X-Content-Type-Options: nosniff`, marker matched |
| 2026-07-05 | Codex    | `848b0613` | existing local stack on `localhost:2026`; system Google Chrome used for browser check | two independent threads + route switch + refresh | pass | Threads `codex-switch-a-20260705024313-ed1c8f` / `codex-switch-b-20260705024313-ed1c8f`; runs `72984a3d-439f-45b0-8998-7735c3019b69` / `5f2786a5-19a1-4bb6-8aac-8083dedf32ea` both reached `success` after `sleep 20`; API messages for both runs contained `run.terminal` and only their own marker; browser loaded both chat routes, switched between them, reloaded each route, and body text showed the matching marker; stable browser console had no errors or duplicate-key warnings |
| 2026-07-05 | Codex    | `93e290bd` | existing local stack on `localhost:2026`; real Command Room usage observed while smoke continued | provider stream reliability | needs follow-up | Thread `3ba75b81-e1c9-4e43-9288-7cbff600fc4f` had recent runs `0e582444-dee2-4190-a65b-e7ad68c754fa` and `54ee1cae-9cca-4be9-a637-5700d3598e22` end as `error` with `Codex API stream ended without response.completed event`; latest DB status counts after recheck were `error=60`, `interrupted=7`, `success=451`, `running=0`, so the failure did not leave the run stuck busy |
| 2026-07-05 | Codex    | `f314d29e` | existing local stack on `localhost:2026`; no new run created | persisted evidence checkpoint | pass | `/health` healthy; DB status counts `error=60`, `interrupted=7`, `success=451`, `running=0`; artifact run `d1a8e6bc-019b-45b8-990d-394865600d8a` still listed one available artifact and download returned `200` with `nosniff` and matching marker; switch runs `72984a3d-439f-45b0-8998-7735c3019b69` / `5f2786a5-19a1-4bb6-8aac-8083dedf32ea` still returned `success`, five visible message rows each, own marker present, opposite marker absent |
| 2026-07-05 | Codex    | `807dbc92` | existing local stack on `localhost:2026`; real Command Room usage observed | provider stream reliability | needs follow-up | Thread `3ba75b81-e1c9-4e43-9288-7cbff600fc4f` run `3797322c-af03-4c56-af7e-06a0f68d8534` briefly appeared `running`, then ended as `error` with `Codex API stream ended without response.completed event`; JSONL contained `run.end` and `run.terminal` with `status=error`, DB updated to `error`, and final status counts were `error=61`, `interrupted=7`, `success=451`, `running=0`; gateway log also showed a memory update `httpx.RemoteProtocolError` / incomplete chunked read for the same thread |
| 2026-07-05 | Codex    | `a7d21a14` | existing local stack on `localhost:2026`; no new run created | persisted evidence checkpoint | pass | 10:51 CST checkpoint: `/health` healthy; DB status counts `error=61`, `interrupted=7`, `success=451`, `running=0`; switch runs `72984a3d-439f-45b0-8998-7735c3019b69` / `5f2786a5-19a1-4bb6-8aac-8083dedf32ea` still returned `success`, five visible message rows each, own marker present, opposite marker absent; artifact run `d1a8e6bc-019b-45b8-990d-394865600d8a` still listed one available artifact and download returned `200` with `nosniff` and matching marker |
| 2026-07-05 | Codex    | `1b00072e` | existing local stack on `localhost:2026`; no new run created | persisted evidence checkpoint | pass | 10:53 CST checkpoint: worktree clean; `/health` healthy; DB status counts unchanged at `error=61`, `interrupted=7`, `success=451`, `running=0`; switch runs `72984a3d-439f-45b0-8998-7735c3019b69` / `5f2786a5-19a1-4bb6-8aac-8083dedf32ea` still returned `success`, five visible message rows each, own marker present, opposite marker absent; artifact run `d1a8e6bc-019b-45b8-990d-394865600d8a` still listed one available artifact and download returned `200` with `nosniff` and matching marker |
| 2026-07-05 | Codex    | `93939422` | existing local stack on `localhost:2026`; no new run created | persisted evidence checkpoint + memory update observation | pass; needs follow-up | 10:54 CST checkpoint: worktree clean; `/health` healthy; DB status counts unchanged at `error=61`, `interrupted=7`, `success=451`, `running=0`; switch runs `72984a3d-439f-45b0-8998-7735c3019b69` / `5f2786a5-19a1-4bb6-8aac-8083dedf32ea` still returned `success`, five visible message rows each, own marker present, opposite marker absent; artifact run `d1a8e6bc-019b-45b8-990d-394865600d8a` still listed one available artifact and download returned `200` with `nosniff` and matching marker; gateway log also showed another memory update `httpx.RemoteProtocolError` / incomplete chunked read for thread `3ba75b81-e1c9-4e43-9288-7cbff600fc4f` at 10:52 CST |

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
- The model-run smoke still logs one React uncontrolled-to-controlled warning;
  it did not block run completion or refresh recovery, but should remain on the
  observation list.
- Cancel currently settles as `interrupted`, not `cancelled`; UI recovery passed,
  but the terminal status naming should be kept visible during beta.
- Direct raw `uvicorn` restart did not preserve the local trusted sandbox mount
  override; use the project launcher or an explicit local `DEER_FLOW_CONFIG_PATH`
  when testing restart against this workstation config.
- The current gateway process still references
  `/tmp/deerflow-gateway-restart-smoke-config.yaml`. Removing that temp config
  while the process is alive keeps `/health` green but makes model/run config
  reads fail with `503 Configuration not available`.

# Model reasoning capability alignment

## Goal

Keep DeerFlow's configured model capabilities, Gateway response, composer
options, runtime request, and delegated subagents consistent for Codex models.

## Capability policy

| Config model | User-selectable reasoning efforts | Default |
| --- | --- | --- |
| `gpt-5.5` | `medium`, `high`, `xhigh` | `xhigh` |
| `gpt-5.6` / GPT-5.6 Sol | `medium`, `high`, `xhigh`, `max` | `max` |
| `gpt-5.6-terra` / GPT-5.6 Terra | `medium`, `high`, `xhigh`, `max` | `max` |

The composer labels remain bilingual: `中 Medium`, `高 High`, `超高 Extra
High`, and `极高 Max`. Codex-provider `ultra` is not exposed. DeerFlow's
existing Command Room `Ultra` mode remains its native subagent-orchestration
mode, not a provider reasoning effort.

## Design

### Configuration and Gateway contract

Add typed `reasoning_efforts` and `default_reasoning_effort` fields to the
model configuration. They are metadata, not constructor arguments for a chat
model. Configure the matrix above in `config.yaml`.

Expose both fields through `/api/models`. The frontend therefore uses the
active Gateway configuration rather than a hard-coded per-model matrix.

### Runtime resolution

Add one backend resolver that accepts a model configuration and an optional
requested effort:

1. Preserve a requested effort when it is in the model's configured list.
2. Otherwise use that model's configured default.
3. Keep internal `none` only for an explicitly disabled-thinking path; do not
   expose it in the composer selector.

The lead agent uses this resolver before model construction. The Codex provider
passes `max` unchanged. It no longer rewrites `max` to `xhigh`; `ultra` never
reaches it from the supported UI/runtime path.

### Frontend

Extend the model API type with the two capability fields. When the selected
model changes, the composer keeps a valid saved effort or resets it to that
model's default. It renders only the selected model's configured efforts with
the bilingual labels above. Old persisted `ultra`, `low`, or other unsupported
values are normalized to the selected model's default before submission.

Command Room may retain `mode: ultra` and `subagent_enabled: true`; this must
not set `reasoning_effort: ultra`.

### Subagents

Pass the parent's resolved reasoning effort into `SubagentExecutor`. It creates
its configured model with thinking enabled and the inherited effective effort.
The same backend resolver applies the subagent model's own capability limit.
Thus a Terra subagent receives `max` by default, while a user-selected lower
effort propagates without exceeding a model's limit.

## Error handling and compatibility

Invalid or stale client values do not fail a run. They normalize to the active
model's configured default. Unknown model names retain the existing model-name
fallback behavior. No provider call is made merely to validate a capability.

## Non-goals

- Do not expose provider `ultra` or delegate it to Codex.
- Do not remove DeerFlow Command Room's native `Ultra` orchestration mode.
- Do not change available model identities, credentials, pricing, or model
  selection permissions.

## Validation

Add focused backend tests for model metadata, effort normalization, `max`
payload preservation, and delegated-agent effort propagation. Add focused
frontend tests for API parsing, per-model options, and stale-value reset. Run
the relevant backend and frontend suites plus local Gateway/frontend health
checks. Do not send a paid model request as part of validation.

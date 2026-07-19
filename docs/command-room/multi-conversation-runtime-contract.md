# Multi-conversation runtime contract

A multi-conversation runtime may associate actions, outputs, artifacts, and observed events with their parent execution context. It may support parallel work and preserve references needed for traceability.

It records facts and enforces only hard access or permission limits. It does not
define work stages, evaluate answer quality, route work to a role, interpret
findings, initiate follow-up work, or determine completion. The Command Room AI
chooses every delegation and makes the final judgment from complete
natural-language returns.

## Thread timeline

`GET /api/threads/{thread_id}/timeline` is the bounded, owner-scoped factual
work record. It returns only persisted `message`, `lifecycle`, and `artifact`
events in immutable thread `seq` order. The record ID is `{thread_id}:{seq}`;
the endpoint never interprets task results or makes decisions about AI work.

An initial request returns the latest bounded window and its `watermark_seq`.
`truncated: true` means older matching facts exist but are outside that window.
The returned opaque cursor is server-HMAC-authenticated and forward-only: use
it for the next incremental read, deduplicate by `event_id`, and order only by
`seq`. When a cursor request returns `409`, discard the local timeline
projection and re-read the initial snapshot. A generic SSE event is only a
prompt to read persisted facts; browser arrival order is never timeline order.

The shared wire contract is
[`contracts/thread_timeline_contract.json`](../../contracts/thread_timeline_contract.json).

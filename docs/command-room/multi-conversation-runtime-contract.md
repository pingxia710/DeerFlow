# Multi-conversation runtime contract

A multi-conversation runtime may associate actions, outputs, artifacts, and observed events with their parent execution context. It may support parallel work and preserve references needed for traceability.

It records facts and enforces hard access or permission limits. For an explicit
Command Room handoff, it may reject unfinished optional Planning/Technical
Design, Review without same-cycle Execution, Execution N+1 without Review N, an
invalid cycle, or an unwritten assigned artifact. It does not evaluate answer
quality, automatically route work to a role, parse findings, initiate rework,
or determine completion. The Command Room AI delegates every role and makes the
final judgment from complete natural-language returns.

## Thread timeline

`GET /api/threads/{thread_id}/timeline` is the bounded, owner-scoped factual
work record. It returns only persisted `message`, `lifecycle`, and `artifact`
events in immutable thread `seq` order. The record ID is `{thread_id}:{seq}`;
the endpoint never interprets task results or derives acceptance, quality,
rework, or the next action.

An initial request returns the latest bounded window and its `watermark_seq`.
`truncated: true` means older matching facts exist but are outside that window.
The returned opaque cursor is server-HMAC-authenticated and forward-only: use
it for the next incremental read, deduplicate by `event_id`, and order only by
`seq`. When a cursor request returns `409`, discard the local timeline
projection and re-read the initial snapshot. A generic SSE event is only a
prompt to read persisted facts; browser arrival order is never timeline order.

The shared wire contract is
[`contracts/thread_timeline_contract.json`](../../contracts/thread_timeline_contract.json).

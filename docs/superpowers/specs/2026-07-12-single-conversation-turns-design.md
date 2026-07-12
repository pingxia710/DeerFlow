# Single-Conversation Turn Design

## Goal

Make a DeerFlow chat read like a normal AI conversation: one user submission and
its AI execution form one visible turn, the reader keeps control of scrolling,
and a completed turn shows a concise time and elapsed duration.

## Scope

This is a frontend presentation change for one chat thread.

- A visible turn is identified by the existing run id, not by Command Room's
  native Round state.
- A turn contains the user message, reasoning/tool/subtask activity, and the
  final assistant output for that run.
- The UI shows a compact turn header and one completion footer.
- The UI follows a live answer only while the reader remains at the live edge.

## Non-Goals

Do not add:

- a backend round API dependency, database schema, migration, or new persisted
  conversation data;
- a top-level turn navigator, collapsed historical turns, or a workflow
  dashboard;
- timestamps on every internal tool event;
- changes to model, Command Room, auth, or run lifecycle behavior.

## Data Model

The frontend already receives the data needed for this view:

- persisted run messages carry `deerflow_run_id` and `history_created_at`;
- run history supplies a `turn_duration` for completed assistant output;
- the live stream supplies the active run id and a local submission time.

Project the flat visible message stream into `ConversationTurn` values in the
frontend. The primary key is `deerflow_run_id`. For legacy rows without that
key, start a turn at each human message and include subsequent visible groups
until the next human message. This fallback is presentation-only and never
writes inferred identifiers back to the server.

## Presentation

Each turn renders as one continuous container:

```
第 3 轮 · 14:22
用户消息
思考 / 工具 / 子任务
最终回复
完成于 14:24 · 用时 2分18秒
```

The header is the only turn marker. Tool cards remain available inside the
turn, but cannot visually detach themselves into another conversation turn.
The footer appears only when the run has a terminal result. Failed or cancelled
runs use the same footer position with their factual status instead of a
success label.

## Scroll Contract

1. Sending a message anchors the new user row near the top of the viewport
   (never the top of the whole transcript), retaining a small visible slice of
   the preceding turn.
2. While the reader is at or near the bottom, streamed output follows normally.
3. Scrolling more than 96px away from the live edge releases follow mode.
   Keyboard scrolling follows the same native scroll path.
4. When follow mode is released, streamed updates and history prepends preserve
   the current visible row. They never force the viewport to the top or bottom.
5. A compact `回到当前回复` control appears while a newer active turn is out of
   view. Using it deliberately resumes follow mode.

The existing `use-stick-to-bottom` dependency remains in use. The change is to
make its follow state explicit, use the existing scroll-to-bottom affordance,
and prevent stream/history rendering from resetting the reader's anchor.

## Time Semantics

- Header time: the user submission time; persisted history uses the first valid
  `history_created_at`, live turns use the local submission time until history
  arrives.
- Completion time: the final visible message time when available, otherwise the
  terminal event time for the active run.
- Elapsed duration: existing `turn_duration`; an active turn shows elapsed time
  without claiming completion.

Invalid or missing timestamps are omitted rather than replaced with misleading
values.

## Components and Data Flow

1. Thread history, live stream, and optimistic messages continue to merge in
   `core/threads/hooks.ts`.
2. A small pure frontend projection groups already-visible messages into
   `ConversationTurn` values.
3. `MessageList` renders a turn shell around the existing message/tool/subtask
   renderers; it does not duplicate their content logic.
4. The conversation scroller receives the active turn anchor and reader-follow
   state. It owns viewport movement; child cards do not initiate broad scroll
   changes.
5. Thread switches clear active-turn and scroll-anchor state, preserving the
   current thread-isolation behavior.

## Edge Cases

- Long-running or interrupted runs keep their turn container and show factual
  in-progress, cancelled, or failed status.
- A history refresh that replaces a run keeps the current visible turn anchored.
- Regeneration creates a new run/turn; superseded output remains governed by
  the existing message visibility rules.
- Existing legacy conversations remain readable through the human-boundary
  fallback.

## Validation

- Unit-test the pure turn projection, including run-id grouping and legacy
  fallback.
- Unit-test time selection and terminal-status labeling.
- Add an E2E flow with a streamed answer: user scrolls up, a tool card changes
  height, and a history page is prepended; the visible anchor must not move.
- Verify deliberate `回到当前回复` resumes follow mode.
- Run the frontend unit tests, type/lint check, and the focused E2E flow.

## Success Criteria

- A reader can identify where each user request starts and where its AI work
  ends without reading tool internals.
- Reading older content is never interrupted by a streaming response.
- The current answer remains easy to return to with one explicit action.
- Time and duration improve scanability without turning the conversation into a
  management dashboard.

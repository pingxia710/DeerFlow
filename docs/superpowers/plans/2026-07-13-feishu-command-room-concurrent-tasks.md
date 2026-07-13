# Feishu Command Room Concurrent Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure a new top-level Feishu Command Room message always starts its own DeerFlow task while replies in an existing Feishu thread keep that task's context.

**Architecture:** Reuse the existing Feishu `message_id` → `topic_id` mapping and the existing manager semaphore (runtime limit 5). Narrow only the pending-clarification fallback: it remains available to ordinary Feishu sessions, but Command Room top-level messages cannot be hijacked by an old pending clarification.

**Tech Stack:** Python 3, pytest, asyncio, Feishu channel parser, DeerFlow `ChannelManager`.

## Global Constraints

- Keep the change in the existing isolated worktree branch `fix/channel-command-room-runtime`.
- Do not add a queue, database field, dependency, or global concurrency setting.
- Preserve explicit Feishu reply-thread mapping and non-Command-Room clarification behavior.
- Do not restart or cancel the live Gateway task during implementation.
- Update `Progress.md` in the same change set.

---

### Task 1: Add the regression test

**Files:**
- Modify: `backend/tests/test_feishu_parser.py` after `test_feishu_plain_reply_consumes_pending_clarification_topic`

**Interfaces:**
- Consumes: `FeishuChannel`, `_make_text_event`, `InboundMessage`, and the existing pending-clarification fixture helper.
- Produces: a failing regression proving Command Room top-level messages keep their own `message_id` topic and leave pending state untouched.

- [ ] **Step 1: Write the failing test**

```python
def test_feishu_command_room_plain_message_starts_new_topic_instead_of_consuming_pending():
    bus = MessageBus()
    channel = FeishuChannel(
        bus,
        {
            "app_id": "test",
            "app_secret": "test",
            "session": {"assistant_id": "command-room"},
        },
    )
    key = channel._pending_key("chat_1", "user_1")
    channel._pending_clarifications[key] = [_pending("om_original", thread_id="deer-thread-1")]

    created: list[InboundMessage] = []

    def fake_make_inbound(**kwargs):
        inbound = InboundMessage(channel_name="feishu", **kwargs)
        created.append(inbound)
        return inbound

    with pytest.MonkeyPatch.context() as m:
        m.setattr(channel, "_make_inbound", fake_make_inbound)
        channel._on_message(_make_text_event("new task", message_id="msg_new"))

    assert created[0].topic_id == "msg_new"
    assert key in channel._pending_clarifications
    assert created[0].metadata[RESOLVED_FROM_PENDING_CLARIFICATION_METADATA_KEY] is False
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `cd backend && uv run pytest tests/test_feishu_parser.py::test_feishu_command_room_plain_message_starts_new_topic_instead_of_consuming_pending -q`

Expected: FAIL because the current parser consumes the pending clarification and changes the topic to `om_original`.

### Task 2: Implement the scoped routing guard

**Files:**
- Modify: `backend/app/channels/feishu.py` near `_pending_key()` and the `_on_message()` pending-clarification branch

**Interfaces:**
- Consumes: the Feishu channel's `config["session"]["assistant_id"]` and existing topic-resolution result.
- Produces: a private Command Room predicate and a routing condition that preserves top-level `message_id` topics for Command Room.

- [ ] **Step 1: Add the minimal session predicate**

Add this method beside `_pending_key()`:

```python
def _is_command_room_session(self) -> bool:
    session = self.config.get("session")
    return isinstance(session, dict) and session.get("assistant_id") == "command-room"
```

- [ ] **Step 2: Guard only the pending fallback**

Change the existing branch from:

```python
if msg_type == InboundMessageType.CHAT and not resolved_from_stored_mapping:
```

to:

```python
if msg_type == InboundMessageType.CHAT and not resolved_from_stored_mapping and not self._is_command_room_session():
```

Do not change `_resolve_topic_id()`, explicit reply mappings, thread storage, or manager concurrency.

- [ ] **Step 3: Run the focused tests to verify the fix**

Run: `cd backend && uv run pytest tests/test_feishu_parser.py::test_feishu_command_room_plain_message_starts_new_topic_instead_of_consuming_pending tests/test_feishu_parser.py::test_feishu_plain_reply_consumes_pending_clarification_topic tests/test_feishu_parser.py::test_feishu_explicit_reply_prefers_stored_mapping_over_pending -q`

Expected: 3 passed.

### Task 3: Record and verify the completed change

**Files:**
- Modify: `Progress.md` at the top
- Test: `backend/tests/test_feishu_parser.py`, `backend/tests/test_channels.py`
- Check: `backend/app/channels/feishu.py`

**Interfaces:**
- Consumes: the passing focused regression and the existing Command Room runtime correction in the worktree.
- Produces: a documented, lint-clean change with no runtime restart.

- [ ] **Step 1: Add the Progress entry**

Record that Command Room top-level Feishu messages no longer consume stale pending clarification state, that reply-thread continuity is preserved, and that no live task was restarted or cancelled.

- [ ] **Step 2: Run the parser and channel suites**

Run: `cd backend && uv run pytest tests/test_feishu_parser.py tests/test_channels.py -q`

Expected: all tests pass; only the suite's existing dependency warnings may remain.

- [ ] **Step 3: Run static and contract checks**

Run:

```bash
cd backend && uv run ruff check app/channels/feishu.py tests/test_feishu_parser.py
cd backend && uv run ruff format --check app/channels/feishu.py tests/test_feishu_parser.py
cd .. && make command-room-contract-check
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 4: Inspect the final worktree**

Run: `git status --short`

Expected: only the intended Progress, Feishu manager/test, design/plan documentation changes are present; no config, credential, main-worktree, or live-service files are changed.

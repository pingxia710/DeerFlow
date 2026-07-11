"""Native round-state persistence.

This is deliberately mechanical: it records lifecycle, associations, and task
lanes. It does not judge quality or choose the next AI role.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.round_state.model import RoundEventRow, RoundRow, TaskLaneRow
from deerflow.runtime.user_context import DEFAULT_USER_ID
from deerflow.utils.time import coerce_iso

ROUND_STATES = frozenset({"open", "executing", "validating", "waiting_user", "awaiting_chair_decision", "closed", "blocked"})
TERMINAL_ROUND_STATES = frozenset({"closed", "blocked"})
ALLOWED_ROUND_TRANSITIONS = {
    "open": frozenset({"executing", "validating", "waiting_user", "awaiting_chair_decision", "closed", "blocked"}),
    "executing": frozenset({"validating", "waiting_user", "awaiting_chair_decision", "closed", "blocked"}),
    "validating": frozenset({"executing", "waiting_user", "awaiting_chair_decision", "closed", "blocked"}),
    "waiting_user": frozenset({"executing", "awaiting_chair_decision", "closed", "blocked"}),
    "awaiting_chair_decision": frozenset({"executing", "waiting_user", "closed", "blocked"}),
    "closed": frozenset({"closed"}),
    "blocked": frozenset({"blocked"}),
}
MAX_INTENT_CHARS = 4000
MAX_NEXT_ACTION_CHARS = 4000


class RoundBindingNotFoundError(LookupError):
    """Raised when an explicit round is outside the requested owner boundary."""


class RoundBindingConflictError(ValueError):
    """Raised when an explicit round exists but cannot accept another run."""


def _explicit_round_id(metadata: dict[str, Any]) -> str | None:
    if "round_id" not in metadata:
        return None
    value = metadata.get("round_id")
    if not isinstance(value, str) or not value.strip():
        raise RoundBindingNotFoundError("Round not found")
    return value.strip()


def _validate_explicit_round(
    *,
    round_id: str,
    row_thread_id: str | None,
    row_user_id: str | None,
    row_state: str | None,
    thread_id: str,
    user_id: str | None,
) -> None:
    if row_thread_id != thread_id or row_user_id != user_id:
        raise RoundBindingNotFoundError(f"Round {round_id} not found")
    if row_state not in ROUND_STATES - TERMINAL_ROUND_STATES:
        raise RoundBindingConflictError(f"Round {round_id} cannot accept a run in state {row_state}")


def _now() -> datetime:
    return datetime.now(UTC)


def _event_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clip(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _row_to_dict(row: RoundRow) -> dict[str, Any]:
    data = row.to_dict()
    for key in ("created_at", "updated_at", "closed_at"):
        if isinstance(data.get(key), datetime):
            data[key] = coerce_iso(data[key])
    return data


def _ref_text(ref: Any) -> str | None:
    if isinstance(ref, str):
        return ref.strip() or None
    if isinstance(ref, dict):
        for key in ("ref", "uri", "url", "path", "artifact_id", "id", "name"):
            value = ref.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _collect_refs(*values: Any, limit: int = 50) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = _ref_text(value)
        if text and text not in seen and len(refs) < limit:
            seen.add(text)
            refs.append(text)

    for value in values:
        if isinstance(value, list):
            for item in value:
                add(item)
        else:
            add(value)
    return refs


def _task_ref_lists(event: dict[str, Any]) -> dict[str, list[str]]:
    action_result = _safe_dict(event.get("action_result"))
    handoff = _safe_dict(event.get("handoff_envelope"))
    evidence_refs = _collect_refs(action_result.get("evidence_refs"), handoff.get("evidenceRefs"), handoff.get("evidence_refs"), event.get("evidence_refs"))
    artifact_refs = _collect_refs(event.get("artifact_refs"), action_result.get("artifact_refs"), handoff.get("artifactRefs"), handoff.get("artifact_refs"))
    output_refs = _collect_refs(action_result.get("output_ref"), action_result.get("output_refs"), handoff.get("outputRefs"), handoff.get("output_refs"), event.get("output_refs"))
    return {"evidence_refs": evidence_refs, "artifact_refs": artifact_refs, "output_refs": output_refs}


def _task_result_ref(event: dict[str, Any]) -> str | None:
    action_result = _safe_dict(event.get("action_result"))
    output_ref = action_result.get("output_ref")
    if isinstance(output_ref, str) and output_ref:
        return output_ref
    refs = event.get("artifact_refs")
    if isinstance(refs, list):
        for ref in refs:
            if isinstance(ref, str) and ref:
                return ref
            if isinstance(ref, dict):
                value = ref.get("artifact_id") or ref.get("path") or ref.get("name")
                if isinstance(value, str) and value:
                    return value
    return None


def _task_evidence_ref(event: dict[str, Any]) -> str | None:
    action_result = _safe_dict(event.get("action_result"))
    refs = action_result.get("evidence_refs")
    if isinstance(refs, list):
        return ", ".join(ref for ref in refs if isinstance(ref, str) and ref) or None
    return None


def _task_handoff(event: dict[str, Any]) -> dict[str, Any] | None:
    handoff = event.get("handoff_envelope")
    return dict(handoff) if isinstance(handoff, dict) and handoff else None


def _assert_allowed_transition(previous: str, state: str) -> None:
    if state not in ROUND_STATES:
        raise ValueError(f"Unknown round state: {state}")
    if state == previous:
        return
    if state not in ALLOWED_ROUND_TRANSITIONS.get(previous, frozenset()):
        raise ValueError(f"Invalid round state transition: {previous} -> {state}")


def _dedupe_refs(values: list[str | None], *, limit: int = 10) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        refs.append(text)
        if len(refs) >= limit:
            break
    return refs


def _task_lane_to_dict(row: TaskLaneRow) -> dict[str, Any]:
    data = row.to_dict()
    data["subagent_type"] = data.get("role")
    data["completed_at"] = data.get("finished_at")
    data["handoff"] = data.pop("handoff_json", None)
    data["evidence_refs"] = data.pop("evidence_refs_json", None) or ([] if data.get("evidence_ref") is None else [data.get("evidence_ref")])
    data["artifact_refs"] = data.pop("artifact_refs_json", None) or ([] if data.get("result_ref") is None else [data.get("result_ref")])
    data["output_refs"] = data.pop("output_refs_json", None) or ([] if data.get("result_ref") is None else [data.get("result_ref")])
    for key in ("created_at", "updated_at", "started_at", "finished_at", "completed_at"):
        if isinstance(data.get(key), datetime):
            data[key] = coerce_iso(data[key])
    return data


class RoundStateRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory
        self._sqlite_write_lock = asyncio.Lock()

    @asynccontextmanager
    async def _seq_write_guard(self, session: AsyncSession):
        """Keep SQLite sequence allocation serialized through transaction commit."""
        bind = session.get_bind()
        if bind is not None and bind.dialect.name == "sqlite":
            async with self._sqlite_write_lock:
                yield
            return
        yield

    @staticmethod
    async def _max_seq_for_round(session: AsyncSession, round_id: str) -> int | None:
        stmt = select(func.max(RoundEventRow.seq)).where(RoundEventRow.round_id == round_id)
        bind = session.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(CAST(:round_id AS text))::bigint)"),
                {"round_id": round_id},
            )
            return await session.scalar(stmt)
        return await session.scalar(stmt.with_for_update())

    async def _append_event(
        self,
        session: AsyncSession,
        *,
        round_id: str,
        thread_id: str,
        event_type: str,
        run_id: str | None = None,
        task_id: str | None = None,
        user_id: str | None = None,
        content: dict[str, Any] | None = None,
    ) -> None:
        max_seq = await self._max_seq_for_round(session, round_id)
        session.add(
            RoundEventRow(
                round_id=round_id,
                thread_id=thread_id,
                run_id=run_id,
                task_id=task_id,
                user_id=user_id,
                event_type=event_type,
                content_json=content or {},
                seq=(max_seq or 0) + 1,
                created_at=_now(),
            )
        )

    async def _latest_round(self, session: AsyncSession, thread_id: str, user_id: str | None) -> RoundRow | None:
        stmt = select(RoundRow).where(RoundRow.thread_id == thread_id)
        if user_id is None:
            stmt = stmt.where(RoundRow.user_id.is_(None))
        else:
            stmt = stmt.where(RoundRow.user_id == user_id)
        stmt = stmt.order_by(RoundRow.updated_at.desc(), RoundRow.created_at.desc()).limit(1)
        return await session.scalar(stmt)

    async def _round_refs(self, session: AsyncSession, round_id: str) -> dict[str, list[str]]:
        rows = (await session.execute(select(TaskLaneRow.artifact_refs_json, TaskLaneRow.evidence_refs_json).where(TaskLaneRow.round_id == round_id).order_by(TaskLaneRow.updated_at.desc()).limit(50))).all()
        artifact_refs = _dedupe_refs([ref for row in rows for ref in ((row[0] if isinstance(row[0], list) else [row[0]]) if row[0] else [])])
        evidence_refs = _dedupe_refs([ref for row in rows for ref in ((row[1] if isinstance(row[1], list) else [row[1]]) if row[1] else [])])
        return {"artifact_refs": artifact_refs, "evidence_refs": evidence_refs}

    async def bind_run(
        self,
        *,
        thread_id: str,
        run_id: str,
        user_id: str | None = None,
        current_intent: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = metadata or {}
        now = _now()
        async with self._sf() as session:
            async with self._seq_write_guard(session), session.begin():
                explicit_round_id = _explicit_round_id(metadata)
                round_row = await session.get(RoundRow, explicit_round_id) if explicit_round_id else None
                if explicit_round_id is not None:
                    _validate_explicit_round(
                        round_id=explicit_round_id,
                        row_thread_id=round_row.thread_id if round_row is not None else None,
                        row_user_id=round_row.user_id if round_row is not None else None,
                        row_state=round_row.state if round_row is not None else None,
                        thread_id=thread_id,
                        user_id=user_id,
                    )
                latest = None if explicit_round_id is not None else await self._latest_round(session, thread_id, user_id)
                created = False
                accepted_next_action = None
                if explicit_round_id is None and round_row is None and latest is not None and latest.state not in TERMINAL_ROUND_STATES:
                    round_row = latest
                previous_run_id = round_row.current_run_id if round_row is not None else None
                previous_intent = round_row.current_intent if round_row is not None else None
                previous_updated_at = coerce_iso(round_row.updated_at) if round_row is not None else None
                if round_row is None:
                    round_row = RoundRow(
                        round_id=str(uuid.uuid4()),
                        thread_id=thread_id,
                        user_id=user_id,
                        parent_round_id=latest.round_id if latest is not None else None,
                        current_run_id=run_id,
                        source_goal_run_id=run_id,
                        current_intent=_clip(current_intent, MAX_INTENT_CHARS),
                        state="open",
                        next_action=None,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(round_row)
                    created = True
                    await self._append_event(
                        session,
                        round_id=round_row.round_id,
                        thread_id=thread_id,
                        run_id=run_id,
                        user_id=user_id,
                        event_type="round.created",
                        content={"parent_round_id": round_row.parent_round_id},
                    )
                else:
                    round_row.current_run_id = run_id
                    round_row.updated_at = now
                    if current_intent:
                        round_row.current_intent = _clip(current_intent, MAX_INTENT_CHARS)

                if current_intent:
                    await self._append_event(
                        session,
                        round_id=round_row.round_id,
                        thread_id=thread_id,
                        run_id=run_id,
                        user_id=user_id,
                        event_type="user.input",
                        content={"current_intent": _clip(current_intent, MAX_INTENT_CHARS)},
                    )
                await self._append_event(
                    session,
                    round_id=round_row.round_id,
                    thread_id=thread_id,
                    run_id=run_id,
                    user_id=user_id,
                    event_type="run.attached",
                    content={
                        "created_round": created,
                        "previous_run_id": previous_run_id,
                        "previous_intent": previous_intent,
                        "previous_updated_at": previous_updated_at,
                    },
                )
                result = _row_to_dict(round_row)
                result["accepted_next_action"] = accepted_next_action
                return result

    async def rollback_run_binding(self, run_id: str) -> bool:
        async with self._sf() as session:
            async with self._seq_write_guard(session), session.begin():
                attachment = await session.scalar(
                    select(RoundEventRow)
                    .where(
                        RoundEventRow.run_id == run_id,
                        RoundEventRow.event_type == "run.attached",
                    )
                    .order_by(RoundEventRow.id.desc())
                    .limit(1)
                )
                if attachment is None:
                    return False
                row = await session.get(RoundRow, attachment.round_id)
                if row is None or row.current_run_id != run_id:
                    return False
                content = attachment.content_json or {}
                if content.get("created_round") is True:
                    await session.execute(delete(TaskLaneRow).where(TaskLaneRow.round_id == row.round_id))
                    await session.execute(delete(RoundEventRow).where(RoundEventRow.round_id == row.round_id))
                    await session.delete(row)
                    return True
                row.current_run_id = content.get("previous_run_id")
                row.current_intent = content.get("previous_intent")
                row.updated_at = _event_time(content.get("previous_updated_at")) or _now()
                await session.execute(
                    delete(TaskLaneRow).where(
                        TaskLaneRow.round_id == row.round_id,
                        TaskLaneRow.run_id == run_id,
                    )
                )
                await session.execute(
                    delete(RoundEventRow).where(
                        RoundEventRow.round_id == row.round_id,
                        RoundEventRow.run_id == run_id,
                    )
                )
                return True

    async def delete_by_thread(self, thread_id: str, *, user_id: str | None = None) -> None:
        async with self._sf() as session:
            async with self._seq_write_guard(session), session.begin():
                owner_filters = (
                    (
                        TaskLaneRow.user_id.is_(None),
                        RoundEventRow.user_id.is_(None),
                        RoundRow.user_id.is_(None),
                    )
                    if user_id is None
                    else (
                        TaskLaneRow.user_id == user_id,
                        RoundEventRow.user_id == user_id,
                        RoundRow.user_id == user_id,
                    )
                )
                await session.execute(delete(TaskLaneRow).where(TaskLaneRow.thread_id == thread_id, owner_filters[0]))
                await session.execute(delete(RoundEventRow).where(RoundEventRow.thread_id == thread_id, owner_filters[1]))
                await session.execute(delete(RoundRow).where(RoundRow.thread_id == thread_id, owner_filters[2]))

    async def delete_legacy_by_thread(self, thread_id: str) -> None:
        await self.delete_by_thread(thread_id, user_id=None)

    async def claim_legacy_by_thread(
        self,
        thread_id: str,
        owner_user_id: str,
    ) -> int:
        async with self._sf() as session:
            async with self._seq_write_guard(session), session.begin():
                round_ids = list(
                    (
                        await session.execute(
                            select(RoundRow.round_id)
                            .where(
                                RoundRow.thread_id == thread_id,
                                or_(
                                    RoundRow.user_id.is_(None),
                                    RoundRow.user_id == DEFAULT_USER_ID,
                                ),
                            )
                            .with_for_update()
                        )
                    ).scalars()
                )
                if not round_ids:
                    return 0
                await session.execute(update(TaskLaneRow).where(TaskLaneRow.round_id.in_(round_ids)).values(user_id=owner_user_id))
                await session.execute(update(RoundEventRow).where(RoundEventRow.round_id.in_(round_ids)).values(user_id=owner_user_id))
                await session.execute(update(RoundRow).where(RoundRow.round_id.in_(round_ids)).values(user_id=owner_user_id, updated_at=_now()))
                return len(round_ids)

    async def list_owners_by_thread(self, thread_id: str) -> set[str | None]:
        async with self._sf() as session:
            owners: set[str | None] = set()
            for model in (RoundRow, RoundEventRow, TaskLaneRow):
                owners.update(await session.scalars(select(model.user_id).where(model.thread_id == thread_id).distinct()))
            return owners

    async def set_run_state(
        self,
        run_id: str,
        *,
        state: str,
        event_type: str,
        content: dict[str, Any] | None = None,
        next_action: str | None = None,
    ) -> dict[str, Any] | None:
        now = _now()
        async with self._sf() as session:
            async with self._seq_write_guard(session), session.begin():
                row = await session.scalar(select(RoundRow).where(RoundRow.current_run_id == run_id).order_by(RoundRow.updated_at.desc()).limit(1))
                if row is None:
                    return None
                previous = row.state
                if previous == state:
                    return {
                        **_row_to_dict(row),
                        **(await self._round_refs(session, row.round_id)),
                    }
                _assert_allowed_transition(previous, state)
                row.state = state
                row.updated_at = now
                if state in TERMINAL_ROUND_STATES and row.closed_at is None:
                    row.closed_at = now
                if state == "closed" and next_action is None:
                    row.next_action = None
                elif next_action:
                    row.next_action = _clip(next_action, MAX_NEXT_ACTION_CHARS)
                await self._append_event(
                    session,
                    round_id=row.round_id,
                    thread_id=row.thread_id,
                    run_id=run_id,
                    user_id=row.user_id,
                    event_type=event_type,
                    content={**(content or {}), "from_state": previous, "to_state": state},
                )
                return {
                    **_row_to_dict(row),
                    **(await self._round_refs(session, row.round_id)),
                }

    async def rollback_terminal_projection(
        self,
        run_id: str,
        *,
        expected_state: str,
        restore_state: str,
    ) -> dict[str, Any] | None:
        if restore_state not in ROUND_STATES - TERMINAL_ROUND_STATES:
            raise ValueError(f"Invalid round rollback state: {restore_state}")
        now = _now()
        async with self._sf() as session:
            async with self._seq_write_guard(session), session.begin():
                row = await session.scalar(select(RoundRow).where(RoundRow.current_run_id == run_id).order_by(RoundRow.updated_at.desc()).limit(1))
                if row is None or row.state != expected_state:
                    return None
                row.state = restore_state
                row.closed_at = None
                row.updated_at = now
                await self._append_event(
                    session,
                    round_id=row.round_id,
                    thread_id=row.thread_id,
                    run_id=run_id,
                    user_id=row.user_id,
                    event_type="run.status_commit_failed",
                    content={
                        "from_state": expected_state,
                        "to_state": restore_state,
                    },
                )
                return {
                    **_row_to_dict(row),
                    **(await self._round_refs(session, row.round_id)),
                }

    async def record_task_events(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        async with self._sf() as session:
            async with self._seq_write_guard(session), session.begin():
                for event in events:
                    thread_id = event.get("thread_id")
                    run_id = event.get("run_id")
                    task_id = event.get("task_id")
                    if not isinstance(thread_id, str) or not isinstance(run_id, str) or not isinstance(task_id, str):
                        continue
                    round_id = event.get("round_id")
                    row = await session.scalar(select(RoundRow).where(RoundRow.current_run_id == run_id).order_by(RoundRow.updated_at.desc()).limit(1))
                    if row is None and isinstance(round_id, str):
                        row = await session.get(RoundRow, round_id)
                    if row is None:
                        continue
                    lane = await session.get(TaskLaneRow, {"thread_id": thread_id, "run_id": run_id, "task_id": task_id})
                    if lane is None:
                        lane = TaskLaneRow(
                            thread_id=thread_id,
                            run_id=run_id,
                            task_id=task_id,
                            round_id=row.round_id,
                            user_id=row.user_id,
                            role=event.get("subagent_type") if isinstance(event.get("subagent_type"), str) else None,
                            status=str(event.get("status") or "in_progress"),
                            description=_clip(event.get("description"), 1000),
                            result=_clip(event.get("result_preview"), 4000),
                            started_at=_event_time(event.get("started_at")),
                            finished_at=_event_time(event.get("finished_at") or event.get("completed_at")),
                            duration_ms=event.get("duration_ms") if isinstance(event.get("duration_ms"), int) and event.get("duration_ms") >= 0 else None,
                            created_at=_now(),
                            updated_at=_now(),
                        )
                        session.add(lane)
                    lane.status = str(event.get("status") or lane.status)
                    lane.role = _clip(event.get("subagent_type"), 64) or lane.role
                    lane.description = _clip(event.get("description"), 1000) or lane.description
                    lane.result = _clip(event.get("result_preview"), 4000) or lane.result
                    lane.started_at = _event_time(event.get("started_at")) or lane.started_at
                    lane.finished_at = _event_time(event.get("finished_at") or event.get("completed_at")) or lane.finished_at
                    duration_ms = event.get("duration_ms")
                    if isinstance(duration_ms, int) and duration_ms >= 0:
                        lane.duration_ms = duration_ms
                    ref_lists = _task_ref_lists(event)
                    lane.result_ref = _task_result_ref(event) or (ref_lists["output_refs"][0] if ref_lists["output_refs"] else None) or lane.result_ref
                    lane.evidence_ref = _task_evidence_ref(event) or (", ".join(ref_lists["evidence_refs"]) if ref_lists["evidence_refs"] else None) or lane.evidence_ref
                    lane.evidence_refs_json = _dedupe_refs([*(lane.evidence_refs_json or []), *ref_lists["evidence_refs"]]) or lane.evidence_refs_json
                    lane.artifact_refs_json = _dedupe_refs([*(lane.artifact_refs_json or []), *ref_lists["artifact_refs"]]) or lane.artifact_refs_json
                    lane.output_refs_json = _dedupe_refs([*(lane.output_refs_json or []), *ref_lists["output_refs"]]) or lane.output_refs_json
                    lane.handoff_json = _task_handoff(event) or lane.handoff_json
                    lane.error = _clip(event.get("error_preview"), 2000) or lane.error
                    lane.updated_at = _now()
                    await self._append_event(
                        session,
                        round_id=row.round_id,
                        thread_id=thread_id,
                        run_id=run_id,
                        task_id=task_id,
                        user_id=row.user_id,
                        event_type=str(event.get("type") or "task.event"),
                        content={
                            "status": lane.status,
                            "role": lane.role,
                            "description": lane.description,
                            "result": lane.result,
                            "started_at": coerce_iso(lane.started_at) if lane.started_at else None,
                            "finished_at": coerce_iso(lane.finished_at) if lane.finished_at else None,
                            "duration_ms": lane.duration_ms,
                            "result_ref": lane.result_ref,
                            "evidence_ref": lane.evidence_ref,
                            "evidence_refs": lane.evidence_refs_json or [],
                            "artifact_refs": lane.artifact_refs_json or [],
                            "output_refs": lane.output_refs_json or [],
                            "handoff": lane.handoff_json,
                        },
                    )

    async def list_by_thread(self, thread_id: str, *, user_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        stmt = select(RoundRow).where(RoundRow.thread_id == thread_id)
        if user_id is None:
            stmt = stmt.where(RoundRow.user_id.is_(None))
        else:
            stmt = stmt.where(RoundRow.user_id == user_id)
        stmt = stmt.order_by(RoundRow.updated_at.desc()).limit(limit)
        async with self._sf() as session:
            rows = list((await session.execute(stmt)).scalars())
            return [{**_row_to_dict(row), **(await self._round_refs(session, row.round_id))} for row in rows]

    async def list_task_lanes_by_round(
        self,
        *,
        thread_id: str,
        round_id: str,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        stmt = select(TaskLaneRow).where(TaskLaneRow.thread_id == thread_id, TaskLaneRow.round_id == round_id)
        if user_id is None:
            stmt = stmt.where(TaskLaneRow.user_id.is_(None))
        else:
            stmt = stmt.where(TaskLaneRow.user_id == user_id)
        stmt = stmt.order_by(TaskLaneRow.updated_at.desc()).limit(limit)
        async with self._sf() as session:
            return [_task_lane_to_dict(row) for row in (await session.execute(stmt)).scalars()]

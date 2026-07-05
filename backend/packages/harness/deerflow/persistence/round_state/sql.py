"""Native round-state persistence.

This is deliberately mechanical: it records lifecycle, associations, and task
lanes. It does not judge quality or choose the next AI role.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.round_state.model import RoundEventRow, RoundRow, TaskLaneRow
from deerflow.utils.time import coerce_iso

ROUND_STATES = frozenset({"open", "executing", "validating", "waiting_user", "closed", "blocked"})
TERMINAL_ROUND_STATES = frozenset({"closed", "blocked"})
ALLOWED_ROUND_TRANSITIONS = {
    "open": frozenset({"executing", "validating", "waiting_user", "closed", "blocked"}),
    "executing": frozenset({"validating", "waiting_user", "closed", "blocked"}),
    "validating": frozenset({"executing", "waiting_user", "closed", "blocked"}),
    "waiting_user": frozenset({"executing", "closed", "blocked"}),
    "closed": frozenset({"closed"}),
    "blocked": frozenset({"blocked"}),
}
MAX_INTENT_CHARS = 4000
MAX_NEXT_ACTION_CHARS = 4000


def _now() -> datetime:
    return datetime.now(UTC)


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
    data["handoff"] = data.pop("handoff_json", None)
    for key in ("created_at", "updated_at"):
        if isinstance(data.get(key), datetime):
            data[key] = coerce_iso(data[key])
    return data


class RoundStateRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

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
        max_seq = await session.scalar(select(func.max(RoundEventRow.seq)).where(RoundEventRow.round_id == round_id))
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
        rows = (await session.execute(select(TaskLaneRow.result_ref, TaskLaneRow.evidence_ref).where(TaskLaneRow.round_id == round_id).order_by(TaskLaneRow.updated_at.desc()).limit(50))).all()
        artifact_refs = _dedupe_refs([row[0] for row in rows])
        evidence_refs = _dedupe_refs([row[1] for row in rows])
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
            async with session.begin():
                explicit_round_id = metadata.get("round_id")
                round_row = await session.get(RoundRow, str(explicit_round_id)) if explicit_round_id else None
                latest = await self._latest_round(session, thread_id, user_id)
                created = False
                accepted_next_action = latest.next_action if latest is not None and latest.state in TERMINAL_ROUND_STATES else None
                if round_row is None and latest is not None and latest.state not in TERMINAL_ROUND_STATES:
                    round_row = latest
                if round_row is None:
                    round_row = RoundRow(
                        round_id=str(explicit_round_id or uuid.uuid4()),
                        thread_id=thread_id,
                        user_id=user_id,
                        parent_round_id=latest.round_id if latest is not None else None,
                        current_run_id=run_id,
                        source_goal_run_id=run_id,
                        current_intent=_clip(current_intent, MAX_INTENT_CHARS),
                        state="open",
                        next_action=accepted_next_action,
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
                    content={"created_round": created},
                )
                result = _row_to_dict(round_row)
                result["accepted_next_action"] = accepted_next_action
                return result

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
            async with session.begin():
                row = await session.scalar(select(RoundRow).where(RoundRow.current_run_id == run_id).order_by(RoundRow.updated_at.desc()).limit(1))
                if row is None:
                    return None
                previous = row.state
                _assert_allowed_transition(previous, state)
                row.state = state
                row.updated_at = now
                if state in TERMINAL_ROUND_STATES and row.closed_at is None:
                    row.closed_at = now
                if next_action:
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
                return {**_row_to_dict(row), **(await self._round_refs(session, row.round_id))}

    async def record_task_events(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        async with self._sf() as session:
            async with session.begin():
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
                            created_at=_now(),
                            updated_at=_now(),
                        )
                        session.add(lane)
                    lane.status = str(event.get("status") or lane.status)
                    lane.result_ref = _task_result_ref(event) or lane.result_ref
                    lane.evidence_ref = _task_evidence_ref(event) or lane.evidence_ref
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
                            "result_ref": lane.result_ref,
                            "evidence_ref": lane.evidence_ref,
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

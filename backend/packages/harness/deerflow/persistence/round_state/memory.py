"""In-memory native round-state store for tests and memory persistence."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from deerflow.persistence.round_state.sql import (
    MAX_INTENT_CHARS,
    _explicit_round_id,
    _validate_explicit_round,
)
from deerflow.runtime.user_context import DEFAULT_USER_ID


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _clip(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


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


class MemoryRoundStateStore:
    def __init__(self) -> None:
        self.rounds: dict[str, dict[str, Any]] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}
        self.task_lanes: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._wake_claim_lock = asyncio.Lock()

    def _latest_round(self, thread_id: str, user_id: str | None) -> dict[str, Any] | None:
        rows = [row for row in self.rounds.values() if row["thread_id"] == thread_id and row.get("user_id") == user_id]
        rows.sort(key=lambda row: row["updated_at"], reverse=True)
        return rows[0] if rows else None

    def _append_event(self, round_id: str, **event: Any) -> None:
        events = self.events.setdefault(round_id, [])
        events.append({"round_id": round_id, "seq": len(events) + 1, "created_at": _now_iso(), **event})

    def _round_refs(self, round_id: str) -> dict[str, list[str]]:
        lanes = [lane for lane in self.task_lanes.values() if lane.get("round_id") == round_id]
        lanes.sort(key=lambda lane: str(lane.get("updated_at") or ""), reverse=True)
        return {
            "artifact_refs": _dedupe_refs([ref for lane in lanes for ref in (lane.get("artifact_refs") or ([lane.get("result_ref")] if lane.get("result_ref") else []))]),
            "evidence_refs": _dedupe_refs([ref for lane in lanes for ref in (lane.get("evidence_refs") or ([lane.get("evidence_ref")] if lane.get("evidence_ref") else []))]),
        }

    def _attached_rounds_for_run(
        self,
        run_id: str,
    ) -> dict[str, dict[str, Any] | None]:
        """Return every durable ``run.attached`` target for a run."""
        bound_rounds: dict[str, dict[str, Any] | None] = {}
        for stored_round_id, events in self.events.items():
            for event in events:
                if event.get("event_type") != "run.attached" or event.get("run_id") != run_id:
                    continue
                round_id = event.get("round_id")
                if not isinstance(round_id, str) or not round_id:
                    round_id = stored_round_id
                bound_rounds.setdefault(round_id, self.rounds.get(round_id))
        return bound_rounds

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
        explicit_round_id = _explicit_round_id(metadata)
        attached_rounds = self._attached_rounds_for_run(run_id)
        if len(attached_rounds) > 1:
            raise ValueError("Run is attached to multiple rounds")
        if attached_rounds:
            attached_round_id, attached_row = next(iter(attached_rounds.items()))
            if attached_row is None:
                raise ValueError("Run is attached to a missing round")
            if attached_row["thread_id"] != thread_id or attached_row.get("user_id") != user_id:
                raise ValueError("Run is already attached to a different thread or user")
            if explicit_round_id is not None and explicit_round_id != attached_round_id:
                raise ValueError("Explicit round_id does not match the run attachment")
            return dict(attached_row)

        latest = self._latest_round(thread_id, user_id)
        row = self.rounds.get(explicit_round_id) if explicit_round_id else None
        if explicit_round_id is not None:
            _validate_explicit_round(
                round_id=explicit_round_id,
                row_thread_id=row.get("thread_id") if row is not None else None,
                row_user_id=row.get("user_id") if row is not None else None,
                thread_id=thread_id,
                user_id=user_id,
            )
        previous_run_id = row.get("current_run_id") if row is not None else None
        previous_intent = row.get("current_intent") if row is not None else None
        previous_updated_at = row.get("updated_at") if row is not None else None
        parent_intent = latest.get("current_intent") if row is None and latest is not None else None
        created = False
        if row is None:
            now = _now_iso()
            row = {
                "round_id": str(uuid.uuid4()),
                "thread_id": thread_id,
                "user_id": user_id,
                "parent_round_id": latest.get("round_id") if latest else None,
                "current_run_id": run_id,
                "source_goal_run_id": run_id,
                "current_intent": _clip(current_intent, MAX_INTENT_CHARS),
                "created_at": now,
                "updated_at": now,
            }
            self.rounds[row["round_id"]] = row
            created = True
            self._append_event(row["round_id"], thread_id=thread_id, run_id=run_id, user_id=user_id, event_type="round.created", content={})
        row["current_run_id"] = run_id
        row["updated_at"] = _now_iso()
        if current_intent:
            row["current_intent"] = _clip(current_intent, MAX_INTENT_CHARS)
            self._append_event(row["round_id"], thread_id=thread_id, run_id=run_id, user_id=user_id, event_type="user.input", content={"current_intent": row["current_intent"]})
        self._append_event(
            row["round_id"],
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
        result = dict(row)
        if isinstance(parent_intent, str) and parent_intent:
            result["parent_intent"] = parent_intent
        return result

    async def rollback_run_binding(self, run_id: str) -> bool:
        attachment = next(
            (event for events in self.events.values() for event in reversed(events) if event.get("run_id") == run_id and event.get("event_type") == "run.attached"),
            None,
        )
        if attachment is None:
            return False
        round_id = attachment["round_id"]
        row = self.rounds.get(round_id)
        if row is None or row.get("current_run_id") != run_id:
            return False
        content = attachment.get("content") or {}
        if content.get("created_round") is True:
            self.rounds.pop(round_id, None)
            self.events.pop(round_id, None)
            for key, lane in list(self.task_lanes.items()):
                if lane.get("round_id") == round_id:
                    self.task_lanes.pop(key, None)
            return True
        row["current_run_id"] = content.get("previous_run_id")
        row["current_intent"] = content.get("previous_intent")
        row["updated_at"] = content.get("previous_updated_at") or _now_iso()
        self.events[round_id] = [event for event in self.events.get(round_id, []) if event.get("run_id") != run_id]
        for key, lane in list(self.task_lanes.items()):
            if lane.get("round_id") == round_id and lane.get("run_id") == run_id:
                self.task_lanes.pop(key, None)
        return True

    async def delete_by_thread(self, thread_id: str, *, user_id: str | None = None) -> None:
        round_ids = {round_id for round_id, row in self.rounds.items() if row.get("thread_id") == thread_id and row.get("user_id") == user_id}
        for key, lane in list(self.task_lanes.items()):
            if lane.get("thread_id") == thread_id and lane.get("user_id") == user_id:
                self.task_lanes.pop(key, None)
        for round_id in round_ids:
            self.events.pop(round_id, None)
            self.rounds.pop(round_id, None)

    async def delete_legacy_by_thread(self, thread_id: str) -> None:
        await self.delete_by_thread(thread_id, user_id=None)

    async def claim_legacy_by_thread(
        self,
        thread_id: str,
        owner_user_id: str,
    ) -> int:
        round_ids = {round_id for round_id, row in self.rounds.items() if row.get("thread_id") == thread_id and row.get("user_id") in {None, DEFAULT_USER_ID}}
        for round_id in round_ids:
            self.rounds[round_id]["user_id"] = owner_user_id
            for event in self.events.get(round_id, []):
                event["user_id"] = owner_user_id
        for lane in self.task_lanes.values():
            if lane.get("round_id") in round_ids:
                lane["user_id"] = owner_user_id
        return len(round_ids)

    async def list_owners_by_thread(self, thread_id: str) -> set[str | None]:
        owners = {row.get("user_id") for row in self.rounds.values() if row.get("thread_id") == thread_id}
        owners.update(event.get("user_id") for events in self.events.values() for event in events if event.get("thread_id") == thread_id)
        owners.update(lane.get("user_id") for lane in self.task_lanes.values() if lane.get("thread_id") == thread_id)
        return owners

    async def record_task_events(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            thread_id = event.get("thread_id")
            run_id = event.get("run_id")
            task_id = event.get("task_id")
            if not isinstance(thread_id, str) or not isinstance(run_id, str) or not isinstance(task_id, str):
                continue
            attached_rounds = self._attached_rounds_for_run(run_id)
            if len(attached_rounds) != 1:
                continue
            row = next(iter(attached_rounds.values()))
            if row is None or row["thread_id"] != thread_id:
                continue
            key = (thread_id, run_id, task_id)
            lane = self.task_lanes.setdefault(
                key,
                {
                    "thread_id": thread_id,
                    "run_id": run_id,
                    "task_id": task_id,
                    "round_id": row["round_id"],
                    "user_id": row.get("user_id"),
                },
            )
            lane["round_id"] = row["round_id"]
            lane["user_id"] = row.get("user_id")
            incoming_status = event.get("status")
            if not (lane.get("status") in {"completed", "failed", "timed_out", "cancelled"} and incoming_status == "in_progress"):
                lane["status"] = str(incoming_status or lane.get("status") or "in_progress")
            lane["role"] = event.get("subagent_type") or lane.get("role")
            lane["subagent_type"] = lane.get("role")
            lane["description"] = _clip(event.get("description"), 1000) or lane.get("description")
            lane["result"] = _clip(event.get("result_preview"), 4000) or lane.get("result")
            lane["started_at"] = event.get("started_at") or lane.get("started_at")
            lane["finished_at"] = event.get("finished_at") or event.get("completed_at") or lane.get("finished_at")
            duration_ms = event.get("duration_ms")
            if isinstance(duration_ms, int) and duration_ms >= 0:
                lane["duration_ms"] = duration_ms
            ref_lists = _task_ref_lists(event)
            lane["result_ref"] = _task_result_ref(event) or (ref_lists["output_refs"][0] if ref_lists["output_refs"] else None) or lane.get("result_ref")
            lane["evidence_ref"] = _task_evidence_ref(event) or (", ".join(ref_lists["evidence_refs"]) if ref_lists["evidence_refs"] else None) or lane.get("evidence_ref")
            lane["evidence_refs"] = _dedupe_refs([*(lane.get("evidence_refs") or []), *ref_lists["evidence_refs"]])
            lane["artifact_refs"] = _dedupe_refs([*(lane.get("artifact_refs") or []), *ref_lists["artifact_refs"]])
            lane["output_refs"] = _dedupe_refs([*(lane.get("output_refs") or []), *ref_lists["output_refs"]])
            handoff = _task_handoff(event)
            if handoff:
                lane["handoff"] = {**(lane.get("handoff") or {}), **handoff}
            lane["error"] = event.get("error_preview") or lane.get("error")
            lane["updated_at"] = _now_iso()
            self._append_event(
                row["round_id"],
                thread_id=thread_id,
                run_id=run_id,
                task_id=task_id,
                user_id=row.get("user_id"),
                event_type=str(event.get("type") or "task.event"),
                content={
                    "status": lane["status"],
                    "role": lane.get("role"),
                    "description": lane.get("description"),
                    "result": lane.get("result"),
                    "started_at": lane.get("started_at"),
                    "finished_at": lane.get("finished_at"),
                    "duration_ms": lane.get("duration_ms"),
                    "result_ref": lane.get("result_ref"),
                    "evidence_ref": lane.get("evidence_ref"),
                    "evidence_refs": lane.get("evidence_refs") or [],
                    "artifact_refs": lane.get("artifact_refs") or [],
                    "output_refs": lane.get("output_refs") or [],
                    "handoff": lane.get("handoff"),
                },
            )

    async def list_by_thread(self, thread_id: str, *, user_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        rows = [row for row in self.rounds.values() if row["thread_id"] == thread_id and row.get("user_id") == user_id]
        rows.sort(key=lambda row: row["updated_at"], reverse=True)
        return [{**row, **self._round_refs(row["round_id"])} for row in rows[:limit]]

    async def list_task_lanes_by_round(
        self,
        *,
        thread_id: str,
        round_id: str,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = [lane for lane in self.task_lanes.values() if lane["thread_id"] == thread_id and lane["round_id"] == round_id and lane.get("user_id") == user_id]
        rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        return [dict(row) for row in rows[:limit]]

    async def get_task_lane(
        self,
        *,
        thread_id: str,
        run_id: str,
        task_id: str,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        lane = self.task_lanes.get((thread_id, run_id, task_id))
        if lane is None or lane.get("user_id") != user_id:
            return None
        return dict(lane)

    async def claim_background_wake(
        self,
        *,
        thread_id: str,
        run_id: str,
        task_id: str,
        user_id: str | None,
        claim_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        async with self._wake_claim_lock:
            lane = self.task_lanes.get((thread_id, run_id, task_id))
            if lane is None or lane.get("user_id") != user_id:
                return False
            expires_at = lane.get("wake_claim_expires_at")
            if lane.get("wake_claim_id") and isinstance(expires_at, datetime) and expires_at > now:
                return False
            lane["wake_claim_id"] = claim_id
            lane["wake_claim_expires_at"] = lease_expires_at
            lane["updated_at"] = _now_iso()
            return True

    async def renew_background_wake_claim(
        self,
        *,
        thread_id: str,
        run_id: str,
        task_id: str,
        user_id: str | None,
        claim_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        async with self._wake_claim_lock:
            lane = self.task_lanes.get((thread_id, run_id, task_id))
            expires_at = lane.get("wake_claim_expires_at") if lane is not None else None
            if lane is None or lane.get("user_id") != user_id or lane.get("wake_claim_id") != claim_id or not isinstance(expires_at, datetime) or expires_at <= now:
                return False
            lane["wake_claim_expires_at"] = lease_expires_at
            lane["updated_at"] = _now_iso()
            return True

    async def persist_claimed_background_wake(
        self,
        *,
        thread_id: str,
        run_id: str,
        task_id: str,
        user_id: str | None,
        claim_id: str,
        now: datetime,
        handoff: dict[str, Any],
        event: dict[str, Any] | None = None,
    ) -> bool:
        async with self._wake_claim_lock:
            lane = self.task_lanes.get((thread_id, run_id, task_id))
            expires_at = lane.get("wake_claim_expires_at") if lane is not None else None
            if lane is None or lane.get("user_id") != user_id or lane.get("wake_claim_id") != claim_id or not isinstance(expires_at, datetime) or expires_at <= now:
                return False
            lane["handoff"] = dict(handoff)
            if event is not None:
                if isinstance(event.get("status"), str):
                    lane["status"] = event["status"]
                if isinstance(event.get("result_preview"), str):
                    lane["result"] = _clip(event["result_preview"], 4000)
                if isinstance(event.get("error_preview"), str):
                    lane["error"] = _clip(event["error_preview"], 2000)
            lane["updated_at"] = _now_iso()
            if event is not None:
                self._append_event(
                    lane["round_id"],
                    thread_id=thread_id,
                    run_id=run_id,
                    task_id=task_id,
                    user_id=lane.get("user_id"),
                    event_type=str(event.get("type") or "task.event"),
                    content={
                        "status": lane.get("status"),
                        "role": lane.get("role"),
                        "description": lane.get("description"),
                        "result": lane.get("result"),
                        "started_at": lane.get("started_at"),
                        "finished_at": lane.get("finished_at"),
                        "duration_ms": lane.get("duration_ms"),
                        "result_ref": lane.get("result_ref"),
                        "evidence_ref": lane.get("evidence_ref"),
                        "evidence_refs": lane.get("evidence_refs") or [],
                        "artifact_refs": lane.get("artifact_refs") or [],
                        "output_refs": lane.get("output_refs") or [],
                        "handoff": lane.get("handoff"),
                    },
                )
            return True

    async def release_background_wake_claim(
        self,
        *,
        thread_id: str,
        run_id: str,
        task_id: str,
        user_id: str | None,
        claim_id: str,
    ) -> None:
        async with self._wake_claim_lock:
            lane = self.task_lanes.get((thread_id, run_id, task_id))
            if lane is not None and lane.get("user_id") == user_id and lane.get("wake_claim_id") == claim_id:
                lane["wake_claim_id"] = None
                lane["wake_claim_expires_at"] = None
                lane["updated_at"] = _now_iso()

    async def list_background_task_lanes(self) -> list[dict[str, Any]]:
        return [dict(lane) for lane in self.task_lanes.values() if isinstance(lane.get("handoff"), dict) and isinstance(lane["handoff"].get("background_recovery"), dict)]

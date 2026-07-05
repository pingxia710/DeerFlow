"""In-memory native round-state store for tests and memory persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from deerflow.persistence.round_state.sql import (
    ALLOWED_ROUND_TRANSITIONS,
    MAX_INTENT_CHARS,
    MAX_NEXT_ACTION_CHARS,
    ROUND_STATES,
    TERMINAL_ROUND_STATES,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _clip(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _assert_allowed_transition(previous: str, state: str) -> None:
    if state not in ROUND_STATES:
        raise ValueError(f"Unknown round state: {state}")
    if state == previous:
        return
    if state not in ALLOWED_ROUND_TRANSITIONS.get(previous, frozenset()):
        raise ValueError(f"Invalid round state transition: {previous} -> {state}")


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
        latest = self._latest_round(thread_id, user_id)
        explicit_round_id = metadata.get("round_id")
        row = self.rounds.get(str(explicit_round_id)) if explicit_round_id else None
        accepted_next_action = latest.get("next_action") if latest and latest["state"] in TERMINAL_ROUND_STATES else None
        if row is None and latest is not None and latest["state"] not in TERMINAL_ROUND_STATES:
            row = latest
        created = False
        if row is None:
            now = _now_iso()
            row = {
                "round_id": str(explicit_round_id or uuid.uuid4()),
                "thread_id": thread_id,
                "user_id": user_id,
                "parent_round_id": latest.get("round_id") if latest else None,
                "current_run_id": run_id,
                "source_goal_run_id": run_id,
                "current_intent": _clip(current_intent, MAX_INTENT_CHARS),
                "state": "open",
                "next_action": accepted_next_action,
                "created_at": now,
                "updated_at": now,
                "closed_at": None,
            }
            self.rounds[row["round_id"]] = row
            created = True
            self._append_event(row["round_id"], thread_id=thread_id, run_id=run_id, user_id=user_id, event_type="round.created", content={})
        row["current_run_id"] = run_id
        row["updated_at"] = _now_iso()
        if current_intent:
            row["current_intent"] = _clip(current_intent, MAX_INTENT_CHARS)
            self._append_event(row["round_id"], thread_id=thread_id, run_id=run_id, user_id=user_id, event_type="user.input", content={"current_intent": row["current_intent"]})
        self._append_event(row["round_id"], thread_id=thread_id, run_id=run_id, user_id=user_id, event_type="run.attached", content={"created_round": created})
        return {**row, "accepted_next_action": accepted_next_action}

    async def set_run_state(
        self,
        run_id: str,
        *,
        state: str,
        event_type: str,
        content: dict[str, Any] | None = None,
        next_action: str | None = None,
    ) -> dict[str, Any] | None:
        row = next((item for item in self.rounds.values() if item.get("current_run_id") == run_id), None)
        if row is None:
            return None
        previous = row["state"]
        _assert_allowed_transition(previous, state)
        row["state"] = state
        row["updated_at"] = _now_iso()
        if state in TERMINAL_ROUND_STATES and row.get("closed_at") is None:
            row["closed_at"] = row["updated_at"]
        if next_action:
            row["next_action"] = _clip(next_action, MAX_NEXT_ACTION_CHARS)
        self._append_event(row["round_id"], thread_id=row["thread_id"], run_id=run_id, user_id=row.get("user_id"), event_type=event_type, content={**(content or {}), "from_state": previous, "to_state": state})
        return {**row, **self._round_refs(row["round_id"])}

    async def record_task_events(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            thread_id = event.get("thread_id")
            run_id = event.get("run_id")
            task_id = event.get("task_id")
            if not isinstance(thread_id, str) or not isinstance(run_id, str) or not isinstance(task_id, str):
                continue
            row = next((item for item in self.rounds.values() if item.get("current_run_id") == run_id), None)
            if row is None:
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
            lane["status"] = str(event.get("status") or lane.get("status") or "in_progress")
            lane["role"] = event.get("subagent_type") or lane.get("role")
            ref_lists = _task_ref_lists(event)
            lane["result_ref"] = _task_result_ref(event) or (ref_lists["output_refs"][0] if ref_lists["output_refs"] else None) or lane.get("result_ref")
            lane["evidence_ref"] = _task_evidence_ref(event) or (", ".join(ref_lists["evidence_refs"]) if ref_lists["evidence_refs"] else None) or lane.get("evidence_ref")
            lane["evidence_refs"] = _dedupe_refs([*(lane.get("evidence_refs") or []), *ref_lists["evidence_refs"]])
            lane["artifact_refs"] = _dedupe_refs([*(lane.get("artifact_refs") or []), *ref_lists["artifact_refs"]])
            lane["output_refs"] = _dedupe_refs([*(lane.get("output_refs") or []), *ref_lists["output_refs"]])
            lane["handoff"] = _task_handoff(event) or lane.get("handoff")
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

"""Objective Command Room RoundRecord persistence.

Round records are audit facts, not a quality gate or a parser for model prose.
They keep identifiers, timestamps, user-goal fingerprints, explicit structured
boundaries, and observed task/action results. Historical records remain readable
unchanged; legacy prose-derived fields are intentionally opaque to this module.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deerflow.command_room.file_records import append_jsonl_record, read_jsonl_text
from deerflow.config.paths import get_paths

logger = logging.getLogger(__name__)

_FIELD_VALUE_LIMIT = 2000


def _sha256_text(text: str | None) -> str | None:
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None


def _truncate(value: Any, limit: int = _FIELD_VALUE_LIMIT) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}... ({len(text)} chars)"


def _text_fingerprint(text: str | None) -> dict[str, Any]:
    return {"sha256": _sha256_text(text), "chars": len(text or "")}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return _json_safe(value.model_dump())
    if hasattr(value, "dict") and callable(value.dict):
        return _json_safe(value.dict())
    return str(value)


def extract_text(content: Any) -> str:
    """Extract plain text without interpreting it as governance semantics."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        pending_str_parts: list[str] = []

        def flush_pending_str_parts() -> None:
            if pending_str_parts:
                pieces.append("".join(pending_str_parts))
                pending_str_parts.clear()

        for block in content:
            if isinstance(block, str):
                pending_str_parts.append(block)
            elif isinstance(block, dict):
                flush_pending_str_parts()
                text = block.get("text")
                if isinstance(text, str):
                    pieces.append(text)

        flush_pending_str_parts()
        return "\n".join(pieces) if pieces else ""
    return str(content) if content is not None else ""


def extract_verdict(text: str | None) -> tuple[str, str]:
    """Legacy API retained for callers; it no longer parses model output."""
    return "", ""


def extract_decision_signal(text: str | None) -> tuple[str, str]:
    """Compatibility alias with no prose-derived decision semantics."""
    return extract_verdict(text)


def evaluate_decision_signals(final_text: str | None, signals: list[dict[str, Any]]) -> dict[str, Any]:
    """Legacy API retained as opaque metadata; no readiness is inferred."""
    return {"legacyOpaque": True}


def evaluate_verdict_gate(final_text: str | None, signals: list[dict[str, Any]]) -> dict[str, Any]:
    """Legacy API retained without verdict or evidence semantics."""
    return evaluate_decision_signals(final_text, signals)


def _load_handoff_records(thread_id: str, user_id: str | None) -> tuple[Path, list[dict[str, Any]]]:
    path = get_paths().thread_dir(thread_id, user_id=user_id) / "audit" / "subagent_handoffs.jsonl"
    text = read_jsonl_text(path)
    if text is None:
        return path, []
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            item = {"status": "invalid_json", "error": str(exc), "rawSha256": _sha256_text(line)}
        if isinstance(item, dict):
            records.append(item)
    return path, records


def _action_fact(record: dict[str, Any]) -> dict[str, Any]:
    result = record.get("action_result")
    action_result = _json_safe(result) if isinstance(result, dict) else {}
    if isinstance(action_result, dict):
        description = _truncate(record.get("description") or "")
        if description and not action_result.get("description"):
            action_result["description"] = description
    return {
        "taskId": str(record.get("task_id") or ""),
        "status": str(record.get("status") or ""),
        "role": str(record.get("subagent_type") or ""),
        "resultRef": {"sha256": record.get("result_sha256"), "chars": record.get("result_chars", 0)},
        "actionResult": action_result or None,
        "error": _truncate(record.get("error") or ""),
    }


def _handoff_facts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_action_fact(record) for record in records]


def _explicit_boundary(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    boundaries: list[dict[str, str]] = []
    for record in records:
        packet = record.get("handoff_packet")
        if isinstance(packet, dict) and packet.get("boundary"):
            boundaries.append({"taskId": str(record.get("task_id") or ""), "value": _truncate(packet["boundary"])})
    return boundaries


def _round_file(thread_id: str, user_id: str | None, base_dir: Path | None = None) -> Path:
    if base_dir is not None:
        return base_dir / "command_room_rounds.jsonl"
    return get_paths().thread_dir(thread_id, user_id=user_id) / "audit" / "command_room_rounds.jsonl"


def record_command_room_round(
    *,
    thread_id: str,
    agent_name: str | None,
    user_id: str | None,
    final_text: str | None,
    user_message: str | None = None,
    run_id: str | None = None,
    usage: dict[str, int] | None = None,
    source: str = "unknown",
    audit_records: list[dict[str, Any]] | None = None,
    base_dir: Path | None = None,
) -> Path | None:
    """Append an objective fact record for command-room runs.

    ``final_text`` is fingerprinted only for traceability. It is never parsed
    for verdicts, cards, evidence strength, role recommendations, or readiness.
    """
    if agent_name != "command-room":
        return None
    handoff_path: Path | None = None
    if audit_records is None:
        handoff_path, audit_records = _load_handoff_records(thread_id, user_id)
    if run_id is not None:
        audit_records = [record for record in audit_records if record.get("run_id") == run_id]

    record = {
        "version": 2,
        "roundId": f"round-{uuid.uuid4().hex}",
        "threadId": thread_id,
        "runId": run_id,
        "agentName": agent_name,
        "source": source,
        "visibility": "internal_audit",
        "hide_from_ui": True,
        "timestamp": datetime.now(UTC).isoformat(),
        "userGoal": _text_fingerprint(user_message),
        "explicitBoundary": _explicit_boundary(audit_records),
        "actionResults": _handoff_facts(audit_records),
        "artifacts": {
            "finalText": _text_fingerprint(final_text),
            "subagentHandoffs": str(handoff_path) if handoff_path is not None else None,
        },
        "usage": _json_safe(usage),
    }
    try:
        return append_jsonl_record(_round_file(thread_id, user_id, base_dir=base_dir), _json_safe(record))
    except Exception:
        logger.debug("Failed to write command-room RoundRecord", exc_info=True)
        return None


def latest_command_room_round(*, thread_id: str, user_id: str | None, base_dir: Path | None = None) -> dict[str, Any] | None:
    path = _round_file(thread_id, user_id, base_dir=base_dir)
    text = read_jsonl_text(path)
    if text is None:
        return None
    latest: dict[str, Any] | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            latest = row
    return latest

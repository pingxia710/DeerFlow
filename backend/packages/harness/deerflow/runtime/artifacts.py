"""Artifact provenance helpers derived from persisted run events."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


def _content_dict(event: dict[str, Any]) -> dict[str, Any]:
    content = event.get("content")
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _string_refs(value: Any) -> list[str]:
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item]
    return []


def _artifact_refs(content: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    refs.extend(_string_refs(content.get("artifact_refs")))
    refs.extend(_string_refs(content.get("artifacts")))

    action_result = content.get("action_result")
    if isinstance(action_result, dict):
        refs.extend(_string_refs(action_result.get("output_ref")))
        refs.extend(_string_refs(action_result.get("evidence_refs")))

    return list(dict.fromkeys(refs))


def build_artifact_index(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a runtime-observed artifact index from persisted run events."""
    entries: list[dict[str, Any]] = []
    for event in events:
        content = _content_dict(event)
        refs = _artifact_refs(content)
        if not refs:
            continue

        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        task_id = content.get("task_id") or metadata.get("task_id")
        source_tool = metadata.get("source_tool")
        if source_tool is None and task_id:
            source_tool = "task"

        for virtual_path in refs:
            entries.append(
                {
                    "user_id": event.get("user_id"),
                    "thread_id": event.get("thread_id"),
                    "run_id": event.get("run_id"),
                    "task_id": task_id if isinstance(task_id, str) and task_id else None,
                    "virtual_path": virtual_path,
                    "created_at": event.get("created_at"),
                    "source_event_type": event.get("event_type"),
                    "source_event_seq": event.get("seq"),
                    "source_tool": source_tool,
                    "source_node": metadata.get("source_node"),
                    "provenance": {
                        "kind": "runtime_observed",
                        "store": "run_events",
                        "caller": metadata.get("caller"),
                    },
                }
            )
    return entries

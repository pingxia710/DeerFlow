"""Artifact provenance helpers derived from persisted run events."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

_VIRTUAL_ARTIFACT_PREFIX = "mnt/user-data/"


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


def _is_virtual_artifact_path(value: str) -> bool:
    stripped = value.lstrip("/")
    return stripped == _VIRTUAL_ARTIFACT_PREFIX.rstrip("/") or stripped.startswith(_VIRTUAL_ARTIFACT_PREFIX)


def _artifact_path_refs(value: Any) -> list[str]:
    if isinstance(value, str) and value:
        return [value] if _is_virtual_artifact_path(value) else []
    if isinstance(value, dict):
        refs: list[str] = []
        for key in ("virtual_path", "path", "output_ref"):
            refs.extend(_artifact_path_refs(value.get(key)))
        return refs
    if isinstance(value, list):
        refs: list[str] = []
        for item in value:
            refs.extend(_artifact_path_refs(item))
        return refs
    return []


def _artifact_refs(content: dict[str, Any]) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    refs.extend((ref, "artifact_refs") for ref in _artifact_path_refs(content.get("artifact_refs")))
    refs.extend((ref, "artifacts") for ref in _artifact_path_refs(content.get("artifacts")))

    action_result = content.get("action_result")
    if isinstance(action_result, dict):
        refs.extend((ref, "action_result.output_ref") for ref in _artifact_path_refs(action_result.get("output_ref")))
        refs.extend((ref, "action_result.evidence_refs") for ref in _artifact_path_refs(action_result.get("evidence_refs")))

    return list(dict.fromkeys(refs))


def build_artifact_index(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a runtime-observed artifact index from persisted run events."""
    entries_by_path: dict[str, dict[str, Any]] = {}
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

        for virtual_path, ref_source in refs:
            entry = {
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
                    "ref_source": ref_source,
                },
            }
            entries_by_path.pop(virtual_path, None)
            entries_by_path[virtual_path] = entry
    return list(entries_by_path.values())

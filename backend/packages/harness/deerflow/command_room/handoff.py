"""Typed AI-to-AI handoff envelope helpers."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_LIST_SPLIT_RE = re.compile(r"[;\n]+")
_EMPTY_ITEM_VALUES = {"", "none", "no", "n/a", "null", "无"}


@dataclass(frozen=True)
class HandoffEnvelope:
    source_role: str = ""
    target_role: str = ""
    task_or_question: str = ""
    context: str = ""
    required_inputs: str = ""
    boundary: str = ""
    boundary_status: str = ""
    expected_evidence: str = ""
    expected_output: str = ""
    evidence_refs: list[str] | None = None
    evidence_strength: str = ""
    output_refs: list[str] | None = None
    handoff_file: str | None = None
    artifact_refs: list[str] | None = None
    released_capabilities: list[str] | None = None
    stop_conditions: list[str] | None = None
    recommended_next_decision: str = ""
    raw_input_ref: str | None = None
    raw_input_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", list(self.evidence_refs or []))
        object.__setattr__(self, "output_refs", list(self.output_refs or []))
        object.__setattr__(self, "artifact_refs", list(self.artifact_refs or []))
        object.__setattr__(self, "released_capabilities", list(self.released_capabilities or []))
        object.__setattr__(self, "stop_conditions", list(self.stop_conditions or []))


def _sha256_text(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _value(packet: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = packet.get(key)
        if value is not None:
            return value
    return None


def _text(packet: Mapping[str, Any], *keys: str) -> str:
    value = _value(packet, *keys)
    return str(value).strip() if value is not None else ""


def _none_if_empty(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text.lower() not in _EMPTY_ITEM_VALUES else None


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        candidates = value
    else:
        candidates = _LIST_SPLIT_RE.split(str(value or ""))
    items: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        text = _none_if_empty(str(candidate).strip(" -*\t"))
        if text and text not in seen:
            seen.add(text)
            items.append(text)
    return items


def handoff_envelope_from_packet(
    packet: Mapping[str, Any],
    *,
    raw_input: str | None = None,
    raw_input_ref: str | None = None,
) -> HandoffEnvelope:
    """Build a typed envelope from explicit AI-authored structured fields."""

    return HandoffEnvelope(
        source_role=_text(packet, "sourceRole", "source_role"),
        target_role=_text(packet, "targetRole", "target_role"),
        task_or_question=_text(packet, "taskOrQuestion", "task_or_question", "goal"),
        context=_text(packet, "context"),
        required_inputs=_text(packet, "requiredInputs", "required_inputs"),
        boundary=_text(packet, "boundary"),
        boundary_status=_text(packet, "boundaryStatus", "boundary_status"),
        expected_evidence=_text(packet, "expectedEvidence", "expected_evidence"),
        expected_output=_text(packet, "expectedOutput", "expected_output"),
        evidence_refs=_list(_value(packet, "evidenceRefs", "evidence_refs")),
        evidence_strength=_text(packet, "evidenceStrength", "evidence_strength"),
        output_refs=_list(_value(packet, "outputRefs", "output_refs")),
        handoff_file=_none_if_empty(_value(packet, "handoffFile", "handoff_file")),
        artifact_refs=_list(_value(packet, "artifactRefs", "artifact_refs")),
        released_capabilities=_list(_value(packet, "releasedCapabilities", "released_capabilities")),
        stop_conditions=_list(_value(packet, "stopConditions", "stop_conditions")),
        recommended_next_decision=_text(packet, "recommendedNextDecision", "recommended_next_decision"),
        raw_input_ref=raw_input_ref or _none_if_empty(_value(packet, "rawInputRef", "raw_input_ref")),
        raw_input_sha256=_text(packet, "rawInputSha256", "raw_input_sha256") or _sha256_text(raw_input),
    )


def handoff_envelope_to_audit_dict(envelope: HandoffEnvelope) -> dict[str, Any]:
    """Serialize an envelope without raw prompt/result bodies."""

    return {
        "sourceRole": envelope.source_role,
        "targetRole": envelope.target_role,
        "taskOrQuestion": envelope.task_or_question,
        "context": envelope.context,
        "requiredInputs": envelope.required_inputs,
        "boundary": envelope.boundary,
        "boundaryStatus": envelope.boundary_status,
        "expectedEvidence": envelope.expected_evidence,
        "expectedOutput": envelope.expected_output,
        "evidenceRefs": envelope.evidence_refs or [],
        "evidenceStrength": envelope.evidence_strength,
        "outputRefs": envelope.output_refs or [],
        "handoffFile": envelope.handoff_file,
        "artifactRefs": envelope.artifact_refs or [],
        "releasedCapabilities": envelope.released_capabilities or [],
        "stopConditions": envelope.stop_conditions or [],
        "recommendedNextDecision": envelope.recommended_next_decision,
        "rawInputRef": envelope.raw_input_ref,
        "rawInputSha256": envelope.raw_input_sha256,
    }

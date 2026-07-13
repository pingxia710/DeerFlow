import dataclasses
import hashlib

import pytest

from deerflow.command_room import HandoffEnvelope, handoff_envelope_from_packet, handoff_envelope_to_audit_dict


def test_handoff_envelope_preserves_explicit_ai_authored_fields():
    packet = {
        "sourceRole": "Planner",
        "targetRole": "Boundary",
        "taskOrQuestion": "check bottom-boundary risk",
        "context": "user asked for implementation",
        "requiredInputs": "development-plan.md",
        "boundary": "no production writes",
        "evidenceRefs": "docs/spec.md; command: pytest -q",
        "evidenceStrength": "AI says this is strong",
        "releasedCapabilities": "file read; local tests",
        "recommendedNextDecision": "NEEDS_MORE",
    }
    raw_input = "natural AI handoff"

    envelope = handoff_envelope_from_packet(packet, raw_input=raw_input)

    assert envelope.source_role == "Planner"
    assert envelope.target_role == "Boundary"
    assert envelope.evidence_refs == ["docs/spec.md", "command: pytest -q"]
    assert envelope.evidence_strength == "AI says this is strong"
    assert envelope.released_capabilities == ["file read", "local tests"]
    assert envelope.recommended_next_decision == "NEEDS_MORE"
    assert envelope.raw_input_sha256 == hashlib.sha256(raw_input.encode("utf-8")).hexdigest()


def test_handoff_envelope_does_not_invent_evidence_strength():
    envelope = handoff_envelope_from_packet({"targetRole": "Evidence", "evidenceRefs": "none"})

    assert envelope.evidence_strength == ""
    assert envelope.evidence_refs == []


def test_handoff_envelope_serialization_stays_compact_and_raw_safe():
    envelope = handoff_envelope_from_packet(
        {"sourceRole": "Planner", "targetRole": "Evidence", "taskOrQuestion": "inspect"},
        raw_input="Raw secret body",
        raw_input_ref="docs/command-room/spec.md",
    )

    payload = handoff_envelope_to_audit_dict(envelope)

    assert payload["sourceRole"] == "Planner"
    assert payload["targetRole"] == "Evidence"
    assert payload["evidenceStrength"] == ""
    assert payload["rawInputRef"] == "docs/command-room/spec.md"
    assert payload["rawInputSha256"] == hashlib.sha256(b"Raw secret body").hexdigest()
    assert "Raw secret body" not in str(payload)


def test_handoff_envelope_is_frozen():
    envelope = HandoffEnvelope(target_role="evidence")

    with pytest.raises(dataclasses.FrozenInstanceError):
        envelope.target_role = "planner"

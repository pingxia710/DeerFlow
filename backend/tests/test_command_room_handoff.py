import dataclasses
import hashlib

import pytest

from deerflow.command_room import HandoffEnvelope, handoff_envelope_from_packet, handoff_envelope_to_audit_dict
from deerflow.subagents.audit import extract_handoff_packet


def test_handoff_envelope_preserves_explicit_fields_and_splits_refs():
    raw_prompt = """Source Role: Planner
Target Role: Boundary
Task/Question: check bottom-boundary risk
Context: user asked for implementation
Required Inputs: development-plan.md
Boundary: no production writes
Boundary Status: unclear
Expected Evidence: source refs and pytest
Expected Output: findings.md
EvidenceRefs: docs/command-room/run-protocol.md:25; command: pytest -q
EvidenceStrength: Strong
OutputRefs: planner-output:round-1
Handoff File: docs/command-room/spec.md
ArtifactRefs: docs/command-room/spec.md; docs/command-room/findings.md
Capabilities: file read; local tests
Stop Conditions: production write; credential exposure
Recommended Next Decision: NEEDS_MORE
"""
    packet = extract_handoff_packet(raw_prompt, description="boundary review", subagent_type="boundary")

    envelope = handoff_envelope_from_packet(packet, raw_input=raw_prompt)

    assert envelope.source_role == "Planner"
    assert envelope.target_role == "Boundary"
    assert envelope.task_or_question == "check bottom-boundary risk"
    assert envelope.context == "user asked for implementation"
    assert envelope.required_inputs == "development-plan.md"
    assert envelope.boundary == "no production writes"
    assert envelope.boundary_status == "unclear"
    assert envelope.expected_evidence == "source refs and pytest"
    assert envelope.expected_output == "findings.md"
    assert envelope.evidence_refs == ["docs/command-room/run-protocol.md:25", "command: pytest -q"]
    assert envelope.evidence_strength == "Strong"
    assert envelope.output_refs == ["planner-output:round-1"]
    assert envelope.handoff_file == "docs/command-room/spec.md"
    assert envelope.artifact_refs == ["docs/command-room/spec.md", "docs/command-room/findings.md"]
    assert envelope.released_capabilities == ["file read", "local tests"]
    assert envelope.stop_conditions == ["production write", "credential exposure"]
    assert envelope.recommended_next_decision == "NEEDS_MORE"
    assert envelope.raw_input_sha256 == hashlib.sha256(raw_prompt.encode("utf-8")).hexdigest()


def test_handoff_envelope_defaults_evidence_strength_to_unverified_and_drops_none_refs():
    packet = extract_handoff_packet(
        "Target Role: Evidence\nEvidenceRefs: none\nOutputRefs: n/a\nArtifactRefs: 无\n",
        description="inspect refs",
        subagent_type="evidence",
    )

    envelope = handoff_envelope_from_packet(packet)

    assert envelope.evidence_strength == "Unverified"
    assert envelope.evidence_refs == []
    assert envelope.output_refs == []
    assert envelope.artifact_refs == []


def test_handoff_envelope_serialization_stays_compact_and_raw_safe():
    raw_prompt = "Source Role: Planner\nTarget Role: Evidence\nTask/Question: inspect\nRaw secret body"
    envelope = handoff_envelope_from_packet(
        extract_handoff_packet(raw_prompt, description="inspect", subagent_type="evidence"),
        raw_input=raw_prompt,
        raw_input_ref="docs/command-room/spec.md",
    )

    payload = handoff_envelope_to_audit_dict(envelope)

    assert payload["sourceRole"] == "Planner"
    assert payload["targetRole"] == "Evidence"
    assert payload["evidenceStrength"] == "Unverified"
    assert payload["rawInputRef"] == "docs/command-room/spec.md"
    assert payload["rawInputSha256"] == hashlib.sha256(raw_prompt.encode("utf-8")).hexdigest()
    assert "Raw secret body" not in str(payload)


def test_handoff_envelope_is_frozen():
    envelope = HandoffEnvelope(target_role="evidence")

    with pytest.raises(dataclasses.FrozenInstanceError):
        envelope.target_role = "planner"

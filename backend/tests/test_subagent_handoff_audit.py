"""Tests for lightweight subagent handoff audit records."""

import json

from deerflow.subagents.audit import extract_handoff_packet, record_subagent_handoff


def test_record_subagent_handoff_omits_raw_payloads(tmp_path):
    raw_prompt = "SECRET_PROMPT_SHOULD_NOT_APPEAR"
    raw_result = """Evidence Signal
Role: fact-finder
Claim: safe claim
EvidenceRefs: test-ref
RedlineTouched: false
RecommendedDecision: PASS

SECRET_RESULT_SHOULD_NOT_APPEAR
"""

    path = record_subagent_handoff(
        thread_id="thread-1",
        run_id="run-1",
        task_id="task-1",
        trace_id="trace-1",
        user_id="user-1",
        subagent_type="fact-finder",
        description="audit test",
        prompt=raw_prompt,
        status="completed",
        result=raw_result,
        error="SECRET_ERROR_SHOULD_NOT_APPEAR",
        usage={"total_tokens": 1},
        action_result={
            "action_id": "task-1",
            "description": "audit test",
            "status": "completed",
            "summary": "SECRET_RESULT_SHOULD_NOT_APPEAR",
            "error": "SECRET_ERROR_SHOULD_NOT_APPEAR",
            "evidence_refs": ["test-ref"],
        },
        base_dir=tmp_path,
    )

    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert "SECRET_PROMPT_SHOULD_NOT_APPEAR" not in text
    assert "SECRET_RESULT_SHOULD_NOT_APPEAR" not in text
    assert "SECRET_ERROR_SHOULD_NOT_APPEAR" not in text

    record = json.loads(text)
    assert record["prompt_sha256"]
    assert record["result_sha256"]
    assert record["error_sha256"]
    assert record["action_result"]["summary_sha256"]
    assert record["action_result"]["error_sha256"]
    assert "summary" not in record["action_result"]
    assert "error" not in record["action_result"]
    assert "signal" not in record
    assert "output_handoff_packet" not in record


def test_record_subagent_handoff_records_compact_handoff_packet(tmp_path):
    raw_prompt = """Goal: inspect the audit path
Inherited Context: backend-only task with known frontend dirty files
Required Inputs: audit.py and round_record.py
Boundary: do not modify scripts/serve.sh or production data
Expected Output: compact audit assertions
Expected Evidence: cite files and tests only
Handoff File: docs/command-room/spec.md
ArtifactRefs: docs/command-room/spec.md; docs/command-room/findings.md
Failure Conditions: stop if unrelated dirty files appear
Tools: read_file, str_replace
Model: inherit
Skill: command-room audit

SECRET_PROMPT_DETAIL_SHOULD_NOT_APPEAR
"""

    path = record_subagent_handoff(
        thread_id="thread-1",
        run_id="run-1",
        task_id="task-1",
        trace_id="trace-1",
        user_id="user-1",
        subagent_type="general-purpose",
        description="audit packet",
        prompt=raw_prompt,
        status="started",
        base_dir=tmp_path,
    )

    text = path.read_text(encoding="utf-8")
    assert "SECRET_PROMPT_DETAIL_SHOULD_NOT_APPEAR" not in text
    record = json.loads(text)
    packet = record["handoff_packet"]
    assert packet["sourceRole"] == "command-room"
    assert packet["targetRole"] == "general-purpose"
    assert packet["taskOrQuestion"] == "audit packet"
    assert packet["goal"] == "audit packet; inspect the audit path"
    assert "known frontend dirty files" in packet["context"]
    assert "audit.py and round_record.py" in packet["requiredInputs"]
    assert "scripts/serve.sh" in packet["boundary"]
    assert "compact audit assertions" in packet["expectedOutput"]
    assert "files and tests" in packet["expectedEvidence"]
    assert packet["handoffFile"] == "docs/command-room/spec.md"
    assert packet["artifactRefs"] == "docs/command-room/spec.md; docs/command-room/findings.md"
    assert "unrelated dirty files" in packet["stopConditions"]
    assert "read_file" in packet["releasedCapabilities"]
    assert "inherit" in packet["releasedCapabilities"]
    assert "command-room audit" in packet["releasedCapabilities"]
    assert "sourceRole" in packet["present"]
    assert "targetRole" in packet["present"]
    assert "taskOrQuestion" in packet["present"]
    assert "context" in packet["present"]
    assert "requiredInputs" in packet["present"]
    assert "expectedOutput" in packet["present"]
    assert "handoffFile" in packet["present"]
    assert "artifactRefs" in packet["present"]


def test_extract_handoff_packet_preserves_ai_to_ai_envelope_fields():
    packet = extract_handoff_packet(
        """Source Role: Planner
Target Role: Boundary
Task/Question: check whether the plan crosses bottom boundaries
EvidenceRefs: docs/command-room/run-protocol.md:25
EvidenceStrength: Strong
OutputRefs: planner-output:round-1
Handoff File: docs/command-room/spec.md
ArtifactRefs: docs/command-room/spec.md; docs/command-room/findings.md
Boundary Status: unclear until Boundary reviews permissions
Recommended Next Decision: NEEDS_MORE
""",
        description="boundary review",
        subagent_type="boundary",
    )

    assert packet["sourceRole"] == "Planner"
    assert packet["targetRole"] == "Boundary"
    assert packet["taskOrQuestion"] == "check whether the plan crosses bottom boundaries"
    assert packet["evidenceRefs"] == "docs/command-room/run-protocol.md:25"
    assert packet["evidenceStrength"] == "Strong"
    assert packet["outputRefs"] == "planner-output:round-1"
    assert packet["handoffFile"] == "docs/command-room/spec.md"
    assert packet["artifactRefs"] == "docs/command-room/spec.md; docs/command-room/findings.md"
    assert packet["boundaryStatus"] == "unclear until Boundary reviews permissions"
    assert packet["recommendedNextDecision"] == "NEEDS_MORE"


def test_record_subagent_handoff_keeps_worker_handoff_text_as_hash_only(tmp_path):
    raw_result = """AI Handoff Envelope
Source Role: Planner
Target Role: Opposition
Task/Question: attack the proposed plan
EvidenceRefs: planner-output:1
EvidenceStrength: Weak
OutputRefs: planner-result:1
Handoff File: docs/command-room/spec.md
ArtifactRefs: docs/command-room/spec.md; docs/command-room/findings.md
Boundary Status: unclear
Recommended Next Decision: NEEDS_MORE
"""

    path = record_subagent_handoff(
        thread_id="thread-1",
        run_id="run-1",
        task_id="task-1",
        trace_id="trace-1",
        user_id="user-1",
        subagent_type="planner",
        description="plan handoff",
        prompt="draft plan",
        status="completed",
        result=raw_result,
        base_dir=tmp_path,
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["result_sha256"]
    assert record["result_chars"] == len(raw_result)
    assert "output_handoff_packet" not in record
    assert "signal" not in record


def test_extract_handoff_packet_supports_context_input_and_stop_aliases():
    packet = extract_handoff_packet(
        """Current Context: prior analysis complete
  with continuation detail
Inputs: config.yaml
- backend/tests/test_x.py
Escalation: ask before production access
Handoff Path: docs/command-room/spec.md
Artifacts: docs/command-room/spec.md
- docs/command-room/findings.md
""",
        description="alias task",
        subagent_type="fact-finder",
    )

    assert packet["context"] == "prior analysis complete; with continuation detail"
    assert packet["requiredInputs"] == "config.yaml; backend/tests/test_x.py"
    assert packet["stopConditions"] == "ask before production access"
    assert packet["handoffFile"] == "docs/command-room/spec.md"
    assert packet["artifactRefs"] == "docs/command-room/spec.md; docs/command-room/findings.md"
    assert packet["present"] == [
        "sourceRole",
        "targetRole",
        "goal",
        "taskOrQuestion",
        "context",
        "requiredInputs",
        "handoffFile",
        "artifactRefs",
        "stopConditions",
        "releasedCapabilities",
    ]


def test_extract_handoff_packet_falls_back_to_description_without_raw_prompt():
    packet = extract_handoff_packet("free-form prompt body", description="short task", subagent_type="opposition")

    assert packet["goal"] == "short task"
    assert packet["sourceRole"] == "command-room"
    assert packet["targetRole"] == "opposition"
    assert packet["taskOrQuestion"] == "short task"
    assert packet["releasedCapabilities"] == "opposition"

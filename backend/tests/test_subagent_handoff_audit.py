"""Tests for factual one-shot subagent audit records."""

import json

from deerflow.subagents.audit import record_subagent_handoff


def test_record_subagent_handoff_hashes_raw_payloads_without_parsing_prose(tmp_path):
    raw_prompt = "Goal: SECRET_PROMPT\nEvidenceStrength: Strong\nRecommendedDecision: PASS"
    raw_result = "SECRET_RESULT\nRecommendedDecision: NEEDS_MORE"

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
        error="SECRET_ERROR",
        usage={"total_tokens": 1},
        action_result={
            "action_id": "task-1",
            "status": "completed",
            "summary": raw_result,
            "error": "SECRET_ERROR",
            "evidence_refs": ["test-ref"],
        },
        base_dir=tmp_path,
    )

    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert "SECRET_PROMPT" not in text
    assert "SECRET_RESULT" not in text
    assert "SECRET_ERROR" not in text
    record = json.loads(text)
    assert record["prompt_sha256"]
    assert record["result_sha256"]
    assert record["error_sha256"]
    assert record["action_result"]["summary_sha256"]
    assert "handoff_packet" not in record
    assert "recommendedNextDecision" not in record
    assert "evidenceStrength" not in record


def test_record_subagent_handoff_preserves_only_explicit_lifecycle_metadata(tmp_path):
    path = record_subagent_handoff(
        thread_id="thread-1",
        run_id="run-1",
        task_id="task-1",
        trace_id="trace-1",
        user_id="user-1",
        subagent_type="opposition",
        description="attack the plan",
        prompt="free-form natural task",
        status="started",
        base_dir=tmp_path,
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["subagent_type"] == "opposition"
    assert record["description"] == "attack the plan"
    assert record["status"] == "started"
    assert record["prompt_chars"] == len("free-form natural task")

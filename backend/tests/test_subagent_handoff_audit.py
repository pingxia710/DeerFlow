"""Tests for lightweight subagent handoff audit records."""

import json

from deerflow.subagents.audit import extract_evidence_signal, record_subagent_handoff


def test_extract_evidence_signal_fields():
    signal = extract_evidence_signal(
        """Evidence Signal
Role: fact-finder
Claim: config is loadable
EvidenceRefs: config.yaml:1055
Unknown/Stale: none
Conflicts: none
RedlineTouched: false
RecommendedDecision: PASS
NextAction: continue
"""
    )

    assert signal["valid"] is True
    assert signal["fields"]["Role"] == "fact-finder"
    assert signal["fields"]["EvidenceRefs"] == "config.yaml:1055"
    assert signal["missing"] == []


def test_extract_evidence_signal_markdown_fields():
    signal = extract_evidence_signal(
        """## Evidence Signal
- **Role:** opposition
- **Claim:** Draft PASS is unsupported.
- **EvidenceRefs:** worker-a: no refs; worker-b: no refs
- **Unknown/Stale:** OAuth evidence missing
- **Conflicts:** Draft next conflicts with read-only boundary.
- **RedlineTouched:** true
- **RecommendedDecision:** STOP_CONFIRM
- **NextAction:** collect concrete evidence refs
"""
    )

    assert signal["valid"] is True
    assert signal["fields"]["Role"] == "opposition"
    assert signal["fields"]["RecommendedDecision"] == "STOP_CONFIRM"
    assert signal["missing"] == []


def test_extract_evidence_signal_without_heading():
    signal = extract_evidence_signal(
        """- Role: opposition
- Claim: Draft PASS is unsupported.
- EvidenceRefs: worker self-claims only
- RedlineTouched: true
- RecommendedDecision: STOP_CONFIRM
"""
    )

    assert signal["valid"] is True
    assert signal["fields"]["EvidenceRefs"] == "worker self-claims only"
    assert signal["missing"] == []


def test_extract_evidence_signal_json_like_fields():
    signal = extract_evidence_signal(
        """{
  "Role": "opposition",
  "Claim": "Draft PASS is unsupported.",
  "EvidenceRefs": "worker self-claims only",
  "RedlineTouched": "true",
  "RecommendedDecision": "STOP_CONFIRM"
}"""
    )

    assert signal["valid"] is True
    assert signal["fields"]["Role"] == "opposition"
    assert signal["fields"]["RecommendedDecision"] == "STOP_CONFIRM"
    assert signal["missing"] == []


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
    assert record["signal"]["valid"] is True
    assert record["signal"]["fields"]["EvidenceRefs"] == "test-ref"


def test_record_subagent_handoff_normalizes_mechanical_signal_fields(tmp_path):
    raw_result = """Evidence Signal
EvidenceRefs: worker self-claims only
RecommendedDecision: NEEDS_MORE because concrete evidence is missing.
NextAction: collect refs
"""

    path = record_subagent_handoff(
        thread_id="thread-1",
        run_id="run-1",
        task_id="task-1",
        trace_id="trace-1",
        user_id="user-1",
        subagent_type="opposition",
        description="反方机制验证",
        prompt="test prompt",
        status="completed",
        result=raw_result,
        base_dir=tmp_path,
    )

    assert path is not None
    record = json.loads(path.read_text(encoding="utf-8"))
    signal = record["signal"]

    assert signal["valid"] is True
    assert signal["missing"] == []
    assert signal["fields"]["Role"] == "opposition"
    assert signal["fields"]["Claim"] == "NEEDS_MORE because concrete evidence is missing."
    assert signal["fields"]["RedlineTouched"] == "false"
    assert signal["derived"] == ["Claim", "RedlineTouched", "Role"]


def test_record_subagent_handoff_accepts_natural_worker_output(tmp_path):
    path = record_subagent_handoff(
        thread_id="thread-1",
        run_id="run-1",
        task_id="task-natural",
        trace_id="trace-1",
        user_id="user-1",
        subagent_type="opposition",
        description="自然反方检查",
        prompt="test prompt",
        status="completed",
        result="这个结论还不能直接进入真实执行，缺少可核查的测试输出和文件引用。",
        base_dir=tmp_path,
    )

    assert path is not None
    record = json.loads(path.read_text(encoding="utf-8"))
    signal = record["signal"]

    assert signal["valid"] is True
    assert signal["missing"] == []
    assert signal["fields"]["Role"] == "opposition"
    assert signal["fields"]["EvidenceRefs"] == "worker-output:task-natural"
    assert signal["fields"]["RecommendedDecision"] == "STOP_CONFIRM"
    assert "EvidenceRefs" in signal["derived"]


def test_record_subagent_handoff_infers_blocking_decision(tmp_path):
    raw_result = """Evidence Signal
EvidenceRefs: worker self-claims only; no logs or outputRefs.
Conflicts: Draft Next enters real execution despite read-only boundary.
RedlineTouched: true
RecommendedDecision:
"""

    path = record_subagent_handoff(
        thread_id="thread-1",
        run_id="run-1",
        task_id="task-1",
        trace_id="trace-1",
        user_id="user-1",
        subagent_type="opposition",
        description="反方机制验证",
        prompt="test prompt",
        status="completed",
        result=raw_result,
        base_dir=tmp_path,
    )

    assert path is not None
    record = json.loads(path.read_text(encoding="utf-8"))
    signal = record["signal"]

    assert signal["valid"] is True
    assert signal["missing"] == []
    assert signal["fields"]["RecommendedDecision"] == "STOP_CONFIRM"
    assert "RecommendedDecision" in signal["derived"]

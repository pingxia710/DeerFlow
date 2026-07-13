from deerflow.command_room.evidence import normalize_evidence_ref


def test_normalizes_explicit_provenance_without_classifying_prose() -> None:
    ref = normalize_evidence_ref(
        "command: python -m pytest; exit code: 0; stdout: 6 passed",
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        task_id="task-1",
        claim="tests completed",
        produced_by="task",
        created_at="2026-01-01T00:00:00+00:00",
    )

    assert ref["source_kind"] == "unknown"
    assert ref["strength"] is None
    assert ref["claim"] == "tests completed"
    assert ref["ref"].startswith("command: python -m pytest")
    assert ref["ref_id"].startswith("evref_")


def test_preserves_explicit_source_kind_and_hash_only() -> None:
    ref = normalize_evidence_ref(
        "artifact: artifacts/result.json sha256:should-not-be-parsed",
        thread_id="thread-1",
        source_kind="artifact",
        sha256="abc123",
    )

    assert ref["source_kind"] == "artifact"
    assert ref["sha256"] == "abc123"
    assert ref["strength"] is None


def test_redacts_secret_and_hidden_reasoning_from_public_fields() -> None:
    ref = normalize_evidence_ref(
        "stdout: api_key=sk-123456789012345 <think>hidden chain</think>visible output",
        thread_id="thread-1",
        source_kind="command_output",
    )

    public_text = f"{ref['ref']} {ref['excerpt']}"
    assert "sk-123456789012345" not in public_text
    assert "hidden chain" not in public_text
    assert "[redacted]" in public_text
    assert "visible output" in public_text

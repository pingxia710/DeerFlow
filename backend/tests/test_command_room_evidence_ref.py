import pytest

from deerflow.command_room.evidence import normalize_evidence_ref


def test_normalizes_old_string_command_output_ref() -> None:
    ref = normalize_evidence_ref(
        "command: python -m pytest tests/test_command_room_evidence.py; exit code: 0; stdout: 6 passed",
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        task_id="task-1",
        claim="tests completed",
        produced_by="task",
        created_at="2026-01-01T00:00:00+00:00",
    )

    assert ref == {
        "ref_id": ref["ref_id"],
        "thread_id": "thread-1",
        "run_id": "run-1",
        "round_id": "round-1",
        "task_id": "task-1",
        "source_kind": "command_output",
        "strength": "Strong",
        "claim": "tests completed",
        "ref": "command: python -m pytest tests/test_command_room_evidence.py; exit code: 0; stdout: 6 passed",
        "excerpt": "command: python -m pytest tests/test_command_room_evidence.py; exit code: 0; stdout: 6 passed",
        "sha256": None,
        "produced_by": "task",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    assert ref["ref_id"].startswith("evref_")


@pytest.mark.parametrize(
    ("raw", "source_kind", "strength"),
    [
        ("artifact: artifacts/result.json sha256:abc123", "artifact", "Strong"),
        ("tests/test_command_room_evidence.py::test_name", "path", "Strong"),
        ("diff --git a/foo.py b/foo.py", "diff", "Strong"),
        ("log: logs/run.log", "log", "Strong"),
        ("tests passed", "self_claim", "Weak"),
        ("output_ref: worker-output-123", "output_ref", "Weak"),
        ("I checked this manually", "unknown", "Unverified"),
    ],
)
def test_normalizes_source_kind_and_mechanical_strength(raw: str, source_kind: str, strength: str) -> None:
    ref = normalize_evidence_ref(raw, thread_id="thread-1", produced_by="runtime")

    assert ref["source_kind"] == source_kind
    assert ref["strength"] == strength


def test_redacts_secret_and_hidden_reasoning_from_public_fields() -> None:
    ref = normalize_evidence_ref(
        "command: cat config; stdout: api_key=sk-123456789012345 <think>hidden chain</think>visible output",
        thread_id="thread-1",
        run_id="run-1",
        source_kind="command_output",
    )

    public_text = f"{ref['ref']} {ref['excerpt']}"
    assert "sk-123456789012345" not in public_text
    assert "hidden chain" not in public_text
    assert "[redacted]" in public_text
    assert "visible output" in public_text


def test_keeps_legacy_string_refs_without_requiring_structured_input() -> None:
    ref = normalize_evidence_ref("source_ref: backend/app.py:12", thread_id="thread-1")

    assert ref["ref"] == "source_ref: backend/app.py:12"
    assert ref["source_kind"] == "source_ref"
    assert ref["strength"] == "Strong"

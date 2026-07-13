from deerflow.command_room.evidence import analyze_evidence_ref, summarize_evidence_refs


def test_tests_passed_alone_is_weak_not_strong():
    signal = analyze_evidence_ref("tests passed")

    assert signal.strong is False
    assert "tests-passed-alone" in signal.weak_reasons
    assert signal.source_kind == "self_claim"


def test_command_with_output_or_exit_code_is_strong():
    signal = analyze_evidence_ref("command: python -m pytest tests/test_command_room_evidence.py; exit code: 0; stdout: 6 passed")

    assert signal.strong is True
    assert "command-output-or-exit-code" in signal.strong_reasons
    assert signal.source_kind == "command_output"


def test_artifact_hash_log_diff_and_path_are_strong_signals():
    refs = [
        "artifact: artifacts/result.json sha256:abc123",
        "logs/run.log",
        "diff --git a/foo.py b/foo.py",
        "tests/test_command_room_evidence.py::test_name",
    ]

    signals = [analyze_evidence_ref(ref) for ref in refs]

    assert all(signal.strong for signal in signals)
    assert [signal.source_kind for signal in signals] == ["artifact", "log", "diff", "path"]


def test_output_ref_only_is_not_evidence():
    signal = analyze_evidence_ref("output_ref: worker-output-123")

    assert signal.strong is False
    assert "output-ref-only" in signal.weak_reasons
    assert signal.source_kind == "output_ref"


def test_worker_output_ref_is_self_attestation_not_strong_evidence():
    signal = analyze_evidence_ref("worker-output:task-123")

    assert signal.strong is False
    assert signal.trusted_source is False
    assert "worker-output-self-attestation" in signal.weak_reasons
    assert signal.source_kind == "self_claim"


def test_summary_does_not_make_quality_verdict_or_auto_rework():
    summary = summarize_evidence_refs(["tests passed", "output_ref: worker-output-123", "worker-output:task-123"])

    assert summary["has_strong_signal"] is False
    assert summary["weak_count"] == 3
    assert summary["quality_verdict"] is None
    assert summary["auto_rework"] is False

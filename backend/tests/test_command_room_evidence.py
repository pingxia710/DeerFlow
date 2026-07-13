from deerflow.command_room.evidence import summarize_evidence_refs


def test_evidence_summary_counts_refs_without_classifying_text():
    summary = summarize_evidence_refs(
        [
            "tests passed",
            "command: python -m pytest; exit code: 0",
            "output_ref: worker-output-123",
            "",
            None,
        ]
    )

    assert summary == {
        "total": 3,
        "refs": [
            "tests passed",
            "command: python -m pytest; exit code: 0",
            "output_ref: worker-output-123",
        ],
        "quality_verdict": None,
        "auto_rework": False,
    }


def test_evidence_summary_does_not_expose_strength_or_trust_fields():
    summary = summarize_evidence_refs(["artifact: result.json", "looks fine"])

    assert "strong_count" not in summary
    assert "weak_count" not in summary
    assert "has_strong_signal" not in summary

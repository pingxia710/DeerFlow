from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "command-room-skill-behavior-probe.py"
SPEC = importlib.util.spec_from_file_location("command_room_skill_behavior_probe", SCRIPT_PATH)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def _review_payload(*, passed: bool) -> dict:
    return {
        "passed": passed,
        "assessment": "Independent AI assessment.",
        "scenario_reviews": [{"id": scenario["id"], "assessment": "Reviewed.", "concerns": []} for scenario in probe.SCENARIOS],
    }


def test_behavior_probe_uses_the_review_ai_verdict(tmp_path, monkeypatch):
    skill = tmp_path / "SKILL.md"
    schema = tmp_path / "schema.json"
    report = tmp_path / "report.json"
    skill.write_text("# Skill\n", encoding="utf-8")
    schema.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(probe, "run_codex", lambda *_args, **_kwargs: _review_payload(passed=True))

    exit_code = probe.main(["--skill", str(skill), "--schema", str(schema), "--out", str(report)])

    assert exit_code == 0
    saved = json.loads(report.read_text(encoding="utf-8"))
    assert saved["passed"] is True
    assert saved["reviewer_response"]["assessment"] == "Independent AI assessment."


def test_behavior_probe_propagates_a_negative_review_ai_verdict(tmp_path, monkeypatch):
    skill = tmp_path / "SKILL.md"
    schema = tmp_path / "schema.json"
    report = tmp_path / "report.json"
    skill.write_text("# Skill\n", encoding="utf-8")
    schema.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(probe, "run_codex", lambda *_args, **_kwargs: _review_payload(passed=False))

    exit_code = probe.main(["--skill", str(skill), "--schema", str(schema), "--out", str(report)])

    assert exit_code == 1
    assert json.loads(report.read_text(encoding="utf-8"))["passed"] is False


def test_behavior_probe_extracts_json_from_fenced_output():
    payload = probe.parse_response('```json\n{"passed": true}\n```\n')
    assert payload == {"passed": True}


def test_behavior_prompt_contains_skill_and_all_scenarios():
    prompt = probe.build_prompt("# NextOS Commander\nKeep the lead brain clear.")

    assert "# NextOS Commander" in prompt
    assert "clear-direct-execution" in prompt
    assert "optional-planning-angles" in prompt
    assert "optional-technical-design" in prompt
    assert "no-program-container-controller" in prompt
    assert "execution-review-rework" in prompt
    assert "bottom-boundary-confirmation" in prompt
    assert "independent review AI" in prompt
    assert "Set `passed` from your own semantic review" in prompt
    assert "Every Execution N is followed by Review N" in prompt
    assert "independent forward and" in prompt
    assert "mandatory planning for a" in prompt
    assert "Do not execute tools" in prompt

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "command-room-skill-behavior-probe.py"
SPEC = importlib.util.spec_from_file_location("command_room_skill_behavior_probe", SCRIPT_PATH)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def _valid_payload():
    return {
        "decisions": [
            {
                "id": "stop-after-observed-implementation",
                "next_action": "finish",
                "dispatch_roles": [],
                "ask_user": False,
                "rationale": "Acceptance evidence is already strong.",
            },
            {
                "id": "small-fact-minimal-path",
                "next_action": "single_subagent",
                "dispatch_roles": ["fact-finder"],
                "ask_user": False,
                "rationale": "One bounded lookup is enough.",
            },
            {
                "id": "bottom-boundary-confirmation",
                "next_action": "stop_confirm",
                "dispatch_roles": [],
                "ask_user": True,
                "rationale": "The requested live change needs confirmation.",
            },
        ]
    }


def test_behavior_probe_accepts_minimal_risk_graded_decisions():
    assert probe.evaluate_decisions(_valid_payload()) == []


def test_behavior_probe_rejects_process_loop_and_unconfirmed_boundary_change():
    payload = _valid_payload()
    payload["decisions"][0]["next_action"] = "multi_subagent"
    payload["decisions"][0]["dispatch_roles"] = ["evidence", "opposition"]
    payload["decisions"][2]["ask_user"] = False

    failures = probe.evaluate_decisions(payload)

    assert any("stop-after-observed-implementation" in failure for failure in failures)
    assert any("bottom-boundary-confirmation" in failure for failure in failures)


def test_behavior_probe_extracts_json_from_fenced_output():
    payload = probe.parse_response('```json\n{"decisions": []}\n```\n')
    assert payload == {"decisions": []}


def test_behavior_prompt_contains_skill_and_all_scenarios():
    prompt = probe.build_prompt("# Naxus Round\nStop dispatching when evidence is strong.")

    assert "# Naxus Round" in prompt
    assert "stop-after-observed-implementation" in prompt
    assert "small-fact-minimal-path" in prompt
    assert "bottom-boundary-confirmation" in prompt
    assert "Do not execute tools" in prompt

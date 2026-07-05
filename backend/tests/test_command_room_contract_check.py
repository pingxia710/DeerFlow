"""Regression tests for scripts/command-room-contract-check.py."""

import copy
import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = REPO_ROOT / "scripts" / "command-room-contract-check.py"
CONTRACT_PATH = REPO_ROOT / "contracts" / "command_room_round_contract.json"

spec = importlib.util.spec_from_file_location("command_room_contract_check", CHECKER_PATH)
assert spec is not None and spec.loader is not None
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _valid_round(contract: dict) -> dict:
    for case in contract["cases"]:
        errors = checker.validate_round(case["round"], contract)
        if case.get("expected_valid") and not errors:
            return copy.deepcopy(case["round"])
    raise AssertionError("contract fixture has no valid baseline round")


def test_all_fixture_cases_match_expected_valid():
    contract = _contract()

    for case in contract["cases"]:
        errors = checker.validate_round(case.get("round") or {}, contract)
        assert (not errors) is bool(case.get("expected_valid")), (case.get("name"), errors)


def test_self_attested_worker_output_only_evidence_cannot_support_pass():
    contract = _contract()
    round_record = _valid_round(contract)
    round_record["decisionSignals"]["decision"] = "PASS"
    round_record["verdict"] = round_record["decisionSignals"]
    round_record["signals"][0].update(
        {
            "evidenceState": "SUPPORTED",
            "selfAttestationOnly": True,
            "evidenceRefs": ["outputRef only", "worker self-claims only"],
            "redlineTouched": False,
            "recommendedDecision": "PASS",
        }
    )

    errors = checker.validate_round(round_record, contract)

    assert any("self-claimed evidence must not be SUPPORTED" in error for error in errors)
    assert any("PASS requires concrete non-self-attested evidenceRefs" in error for error in errors)


def test_target_role_and_round_advisory_gated_false_are_not_hard_gate_failures():
    contract = _contract()
    round_record = _valid_round(contract)
    round_record["roundContextAvailable"] = False
    round_record["roundRequired"] = False
    round_record["dispatchPlan"][0]["role"] = "target-opposition"
    round_record["signals"][0]["role"] = "target-opposition"
    round_record["decisionSignals"]["gated"] = False
    round_record["verdict"] = round_record["decisionSignals"]

    errors = checker.validate_round(round_record, contract)

    assert errors == []


def test_invalid_alias_and_missing_required_field_are_reported():
    contract = _contract()
    round_record = _valid_round(contract)
    round_record["compatibilityAliases"]["verdict"] = "hardGate"
    del round_record["nextRoundContract"]["nextGoal"]

    errors = checker.validate_round(round_record, contract)

    assert "compatibilityAliases.verdict must point to decisionSignals" in errors
    assert "nextRoundContract missing fields: nextGoal" in errors

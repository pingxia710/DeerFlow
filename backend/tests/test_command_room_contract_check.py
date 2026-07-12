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
        if case.get("expected_valid"):
            return copy.deepcopy(case["round"])
    raise AssertionError("contract fixture has no valid baseline round")


def test_all_fixture_cases_match_expected_valid():
    contract = _contract()
    for case in contract["cases"]:
        errors = checker.validate_round(case.get("round") or {}, contract)
        assert (not errors) is bool(case.get("expected_valid")), (case.get("name"), errors)


def test_quality_gate_fields_are_rejected_from_fact_record():
    contract = _contract()
    round_record = _valid_round(contract)
    round_record["verdict"] = {"decision": "PASS"}
    round_record["decisionSignals"] = {"gated": False}

    errors = checker.validate_round(round_record, contract)

    assert "round contains prohibited fact-contract fields: decisionSignals, verdict" in errors


def test_missing_objective_action_fact_is_reported():
    contract = _contract()
    round_record = _valid_round(contract)
    del round_record["actionResults"][0]["status"]

    errors = checker.validate_round(round_record, contract)

    assert "actionResults[0] missing fields: status" in errors

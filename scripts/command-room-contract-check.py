#!/usr/bin/env python3
"""Validate the objective Command Room round-record fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "command_room_round_contract.json"


def _has_required_value(obj: dict[str, Any], field: str) -> bool:
    if field not in obj or obj[field] is None:
        return False
    if field == "error":
        return True
    return bool(obj[field].strip()) if isinstance(obj[field], str) else True


def _missing_fields(obj: dict[str, Any], fields: list[str]) -> list[str]:
    return [field for field in fields if not _has_required_value(obj, field)]


def validate_round(round_record: dict[str, Any], contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = _missing_fields(round_record, contract["required_round_fields"])
    if missing:
        return [f"round missing fields: {', '.join(missing)}"]

    prohibited = sorted(set(contract.get("prohibited_round_fields", [])).intersection(round_record))
    if prohibited:
        errors.append(f"round contains prohibited fact-contract fields: {', '.join(prohibited)}")

    goal = round_record.get("userGoal")
    if not isinstance(goal, dict):
        errors.append("userGoal must be an object")
    else:
        missing_goal = _missing_fields(goal, contract["required_goal_fingerprint_fields"])
        if missing_goal:
            errors.append(f"userGoal missing fields: {', '.join(missing_goal)}")

    if not isinstance(round_record.get("explicitBoundary"), list):
        errors.append("explicitBoundary must be a list")
    actions = round_record.get("actionResults")
    if not isinstance(actions, list):
        errors.append("actionResults must be a list")
    else:
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                errors.append(f"actionResults[{index}] is not an object")
                continue
            missing_action = _missing_fields(action, contract["required_action_result_fields"])
            if missing_action:
                errors.append(f"actionResults[{index}] missing fields: {', '.join(missing_action)}")
            if not isinstance(action.get("resultRef"), dict):
                errors.append(f"actionResults[{index}].resultRef must be an object")
    if not isinstance(round_record.get("artifacts"), dict):
        errors.append("artifacts must be an object")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    args = parser.parse_args(argv)
    contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    failures: list[str] = []
    for case in contract.get("cases", []):
        errors = validate_round(case.get("round") or {}, contract)
        if (not errors) != bool(case.get("expected_valid")):
            failures.append(f"{case.get('name', '<unnamed>')}: {errors}")
        else:
            print(f"OK {case.get('name', '<unnamed>')}")
    if failures:
        print("Command-room audit fixture inspection found mismatches:", file=sys.stderr)
        print("\n".join(f"- {failure}" for failure in failures), file=sys.stderr)
        return 1
    print("Command-room audit fixture inspection completed without mismatches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

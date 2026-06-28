#!/usr/bin/env python3
"""Validate the internal command-room audit fixture."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

DEFAULT_CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "command_room_round_contract.json"


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _has_required_value(obj: dict[str, Any], field: str) -> bool:
    if field not in obj or obj[field] is None:
        return False
    if isinstance(obj[field], str):
        return bool(obj[field].strip())
    return True


def _missing_fields(obj: dict[str, Any], fields: list[str]) -> list[str]:
    return [field for field in fields if not _has_required_value(obj, field)]


def _decision(value: Any) -> str:
    return str(value or "").strip().upper()


_WEAK_EVIDENCE_RE = re.compile(
    r"^\s*$|^\s*(?:none|no\s+refs?|no\s+evidence|n/a|null|无|无证据)\s*$|"
    r"worker\s+says\s+done|worker\s+self|worker\s*自证|self[-\s]?claim|self[-\s]?attestation|"
    r"summary\s+only|task\s+(?:completed|done)|completed\s+task|output\s*ref\s+only|outputRef\s+only|"
    r"provided\s+worker\s+claims\s+only|worker\s+self-claims\s+only",
    re.IGNORECASE,
)
_STRONG_EVIDENCE_RE = re.compile(
    r"\b(?:file|command|cmd|test|pytest|log|artifact|screenshot|external|url|http[s]?://)\b|"
    r"(?:^|[\s`'\"])[\w./-]+\.(?:py|ts|tsx|js|jsx|json|ya?ml|toml|md|txt|log)(?::|\b)|"
    r"::test_|\bpassed\b|\bexit\s*code\s*0\b",
    re.IGNORECASE,
)


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"true", "yes", "y", "1", "是", "触及", "touched"}


def _has_concrete_evidence_refs(value: Any) -> bool:
    refs = [str(item) for item in value] if isinstance(value, list) else [str(value or "")]
    return any(_STRONG_EVIDENCE_RE.search(ref) and not _WEAK_EVIDENCE_RE.search(ref) for ref in refs)


def _self_attestation_only(signal: dict[str, Any]) -> bool:
    if _truthy(signal.get("selfAttestationOnly")):
        return True
    refs = _as_list(signal.get("evidenceRefs"))
    if not refs:
        return True
    return not _has_concrete_evidence_refs(refs) and all(_WEAK_EVIDENCE_RE.search(str(ref)) for ref in refs)


def validate_round(round_record: dict[str, Any], contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    missing = _missing_fields(round_record, contract["required_round_fields"])
    if missing:
        errors.append(f"round missing fields: {', '.join(missing)}")
        return errors

    allowed_verdicts = set(contract["allowed_verdicts"])
    blocking_decisions = set(contract["blocking_recommended_decisions"])
    signal_required = contract["required_signal_fields"]
    prohibited_signal_fields = set(contract["prohibited_signal_fields"])

    dispatch_plan = _as_list(round_record["dispatchPlan"])
    lane_ids: set[str] = set()
    for index, lane in enumerate(dispatch_plan):
        if not isinstance(lane, dict):
            errors.append(f"dispatchPlan[{index}] is not an object")
            continue
        missing = _missing_fields(lane, contract["required_dispatch_lane_fields"])
        if missing:
            errors.append(f"dispatchPlan[{index}] missing fields: {', '.join(missing)}")
        lane_id = str(lane.get("laneId") or "")
        if lane_id in lane_ids:
            errors.append(f"duplicate laneId: {lane_id}")
        if lane_id:
            lane_ids.add(lane_id)
        if not isinstance(lane.get("allowedTools"), list):
            errors.append(f"dispatchPlan[{index}].allowedTools must be a list")

    signals = _as_list(round_record["signals"])
    has_opposition_signal = False
    blocking_opposition = False
    pass_concrete_evidence = False
    stale_or_self_attested_signal = False
    redline_signal = False
    for index, signal in enumerate(signals):
        if not isinstance(signal, dict):
            errors.append(f"signals[{index}] is not an object")
            continue
        missing = _missing_fields(signal, signal_required)
        if missing:
            errors.append(f"signals[{index}] missing fields: {', '.join(missing)}")
        unexpected = sorted(prohibited_signal_fields.intersection(signal))
        if unexpected:
            errors.append(f"signals[{index}] contains prohibited fields: {', '.join(unexpected)}")
        lane_id = str(signal.get("laneId") or "")
        if lane_id:
            if lane_ids and lane_id not in lane_ids:
                errors.append(f"signals[{index}] references unknown laneId: {lane_id}")
        if not isinstance(signal.get("evidenceRefs"), list) or not signal.get("evidenceRefs"):
            errors.append(f"signals[{index}].evidenceRefs must be a non-empty list")

        evidence_state = _decision(signal.get("evidenceState"))
        if evidence_state not in {"SUPPORTED", "STALE", "CONFLICTED", "REDLINE"}:
            errors.append(f"signals[{index}].evidenceState is invalid: {signal.get('evidenceState')!r}")
        if not isinstance(signal.get("selfAttestationOnly"), bool):
            errors.append(f"signals[{index}].selfAttestationOnly must be boolean")
        pass_concrete_evidence = pass_concrete_evidence or (
            evidence_state == "SUPPORTED"
            and not _self_attestation_only(signal)
            and _has_concrete_evidence_refs(signal.get("evidenceRefs"))
        )
        stale_or_self_attested_signal = stale_or_self_attested_signal or evidence_state in {"STALE", "CONFLICTED"} or _self_attestation_only(signal)
        redline_signal = redline_signal or bool(signal.get("redlineTouched")) or evidence_state == "REDLINE"

        if signal.get("role") == "opposition":
            has_opposition_signal = True
            if _decision(signal.get("recommendedDecision")) in blocking_decisions:
                blocking_opposition = True

    verdict = round_record["verdict"]
    if not isinstance(verdict, dict):
        errors.append("verdict must be an object")
        return errors

    verdict_decision = _decision(verdict.get("decision"))
    if verdict_decision not in allowed_verdicts:
        errors.append(f"invalid verdict decision: {verdict.get('decision')!r}")

    verdict_refs = _as_list(verdict.get("evidenceRefs"))
    if verdict_decision == "PASS":
        exemption = round_record.get("oppositionExemption")
        has_exemption = isinstance(exemption, dict) and not _missing_fields(
            exemption,
            contract["required_opposition_exemption_fields"],
        )
        if not has_opposition_signal and not has_exemption:
            errors.append("PASS requires an opposition signal or a complete oppositionExemption")
        if not verdict_refs:
            errors.append("PASS requires verdict.evidenceRefs")
        if not pass_concrete_evidence:
            errors.append("PASS requires concrete non-self-attested evidenceRefs")
        if stale_or_self_attested_signal:
            errors.append("PASS is invalid with STALE/CONFLICTED or selfAttestationOnly signals")

    if blocking_opposition and verdict_decision == "PASS":
        errors.append("PASS is invalid while opposition recommends a blocking decision")

    if redline_signal and verdict_decision == "PASS":
        errors.append("PASS is invalid while a signal has redlineTouched=true or evidenceState=REDLINE")

    next_round = round_record["nextRoundContract"]
    if not isinstance(next_round, dict):
        errors.append("nextRoundContract must be an object")
    else:
        missing = _missing_fields(next_round, contract["required_next_round_fields"])
        if missing:
            errors.append(f"nextRoundContract missing fields: {', '.join(missing)}")
        if not isinstance(next_round.get("userConfirmationNeeded"), bool):
            errors.append("nextRoundContract.userConfirmationNeeded must be boolean")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT), help="Path to the contract fixture JSON.")
    args = parser.parse_args(argv)

    contract_path = Path(args.contract)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))

    failures: list[str] = []
    for case in contract.get("cases", []):
        name = case.get("name", "<unnamed>")
        expected_valid = bool(case.get("expected_valid"))
        errors = validate_round(case.get("round") or {}, contract)
        actual_valid = not errors
        if actual_valid != expected_valid:
            status = "valid" if actual_valid else "invalid"
            failures.append(f"{name}: expected_valid={expected_valid}, got {status}: {errors}")
        else:
            print(f"OK {name}")

    if failures:
        print("Command-room audit fixture inspection found mismatches:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("Command-room audit fixture inspection completed without mismatches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

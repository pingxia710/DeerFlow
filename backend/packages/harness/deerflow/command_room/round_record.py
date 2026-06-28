"""Minimal Command Room RoundRecord persistence and readiness signals.

The Command Room may choose collaborators freely. Persisted state is limited to
readiness/evidence/risk signals and notable gaps; it does not automatically judge
quality, force opposition, trigger rework, or replace the lead AI's final call.
Raw worker/user transcripts are kept out of the round ledger.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import uuid
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deerflow.command_room.round_context import (
    create_round_context,
    record_action_result_from_event,
    round_context_signals,
)
from deerflow.config.paths import get_paths

logger = logging.getLogger(__name__)

_WRITE_LOCK = threading.Lock()
_FIELD_VALUE_LIMIT = 2000
_BLOCKING_DECISIONS = {"NEEDS_MORE", "BLOCKED", "STOP_CONFIRM"}
_ALLOWED_VERDICTS = {"PASS", "NEEDS_MORE", "BLOCKED", "STOP_CONFIRM"}
_STALE_STATES = {"STALE", "CONFLICTED"}
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
_VERDICT_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:\*\*)?Verdict(?:\*\*)?\s*[:：]\s*(?:[`*]+)?\s*(.*?)(?:[`*]+)?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_ROUND_CARD_FIELD_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:\*\*)?"
    r"(Goal|Boundary|Dispatch|Evidence|Opposition|Next)"
    r"(?:\*\*)?\s*[:：]\s*(.*)$",
    re.IGNORECASE,
)


def _sha256_text(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _truncate(value: str | None, limit: int = _FIELD_VALUE_LIMIT) -> str:
    if not value:
        return ""
    clean = value.strip()
    if len(clean) <= limit:
        return clean
    return f"{clean[:limit]}... ({len(clean)} chars)"


def _text_fingerprint(text: str | None) -> dict[str, Any]:
    return {
        "sha256": _sha256_text(text),
        "chars": len(text or ""),
    }


def _json_safe(value: Any) -> Any:
    """Recursively convert structured objects to JSON-safe values.

    Preserve evidence signal structure instead of stringifying unknown objects.
    """
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return _json_safe(value.model_dump())
    if hasattr(value, "dict") and callable(value.dict):
        return _json_safe(value.dict())
    return value


def extract_text(content: Any) -> str:
    """Extract plain text from LangChain message content without importing client."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        pending_str_parts: list[str] = []

        def flush_pending_str_parts() -> None:
            if pending_str_parts:
                pieces.append("".join(pending_str_parts))
                pending_str_parts.clear()

        for block in content:
            if isinstance(block, str):
                pending_str_parts.append(block)
            elif isinstance(block, dict):
                flush_pending_str_parts()
                text_val = block.get("text")
                if isinstance(text_val, str):
                    pieces.append(text_val)

        flush_pending_str_parts()
        return "\n".join(pieces) if pieces else ""
    return str(content) if content is not None else ""


def extract_verdict(text: str | None) -> tuple[str, str]:
    """Return the final visible Verdict line and normalized decision."""
    if not text:
        return "", "NEEDS_MORE"

    matches = list(_VERDICT_RE.finditer(text))
    if not matches:
        return "", "NEEDS_MORE"

    value = matches[-1].group(1).strip()
    upper = value.upper()
    for verdict in _ALLOWED_VERDICTS:
        if re.search(rf"\b{re.escape(verdict)}\b", upper):
            return f"Verdict: {value}", verdict

    if re.search(r"不能\s*PASS|不可\s*PASS|不应\s*PASS|不能判定\s*PASS", value, re.IGNORECASE):
        return f"Verdict: {value}", "NEEDS_MORE"
    return f"Verdict: {value}", "NEEDS_MORE"


def _extract_round_card_sections(text: str | None) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in (text or "").splitlines():
        match = _ROUND_CARD_FIELD_RE.match(line)
        if match:
            current = match.group(1).lower()
            first = match.group(2).strip()
            sections[current] = [first] if first else []
            continue
        if current is not None and not line.strip():
            current = None
            continue
        if current is not None:
            sections[current].append(line)
    return {key: _truncate("\n".join(value)) for key, value in sections.items()}


def _truthy(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"true", "yes", "y", "1", "是", "触及", "touched"}


def _as_evidence_refs(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_truncate(str(item), 500) for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "无", "n/a", "null"}:
        return []
    refs = [_truncate(item.strip(" -*\t"), 500) for item in re.split(r"[;\n]+", text) if item.strip(" -*\t")]
    return refs or [_truncate(text, 1000)]


def _has_strong_evidence_ref(refs: list[str]) -> bool:
    return any(_STRONG_EVIDENCE_RE.search(ref) and not _WEAK_EVIDENCE_RE.search(ref) for ref in refs)


def _self_attestation_only(refs: list[str], value: Any = None) -> bool:
    if str(value or "").strip().lower() in {"true", "yes", "y", "1", "是"}:
        return True
    if not refs:
        return True
    return not _has_strong_evidence_ref(refs) and all(_WEAK_EVIDENCE_RE.search(ref) for ref in refs)


def _signal_state(fields: dict[str, Any], refs: list[str]) -> tuple[str, bool]:
    explicit = str(fields.get("evidenceState") or "").strip().upper()
    self_only = _self_attestation_only(refs, fields.get("selfAttestationOnly"))
    if explicit in {"SUPPORTED", "STALE", "CONFLICTED", "REDLINE"}:
        return explicit, self_only or explicit == "STALE"
    if _truthy(fields.get("RedlineTouched")):
        return "REDLINE", self_only
    if self_only:
        return "STALE", True
    conflicts = str(fields.get("Conflicts") or "").strip().lower()
    if conflicts and conflicts not in {"none", "无", "n/a", "null"}:
        return "CONFLICTED", self_only
    return "SUPPORTED", False


def _signal_meta_value(signal: dict[str, Any], fields: dict[str, Any], key: str) -> Any:
    return signal.get(key, fields.get(key))


def _load_handoff_records(thread_id: str, user_id: str | None) -> tuple[Path, list[dict[str, Any]]]:
    path = get_paths().thread_dir(thread_id, user_id=user_id) / "audit" / "subagent_handoffs.jsonl"
    if not path.exists():
        return path, []

    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            item = {"status": "invalid_json", "error": str(exc), "rawSha256": _sha256_text(line)}
        if isinstance(item, dict):
            records.append(item)
    return path, records


def _dispatch_plan_from_handoffs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_task: dict[str, dict[str, Any]] = {}
    for record in records:
        task_id = str(record.get("task_id") or "")
        if not task_id:
            continue
        lane = by_task.setdefault(
            task_id,
            {
                "laneId": task_id,
                "role": str(record.get("subagent_type") or ""),
                "task": _truncate(str(record.get("description") or "")),
                "input": {},
                "allowedTools": [],
                "evidenceExpected": ["Evidence Signal"],
                "redlineScope": [],
            },
        )
        lane["status"] = record.get("status") or lane.get("status")
        lane["input"]["prompt"] = {
            "sha256": record.get("prompt_sha256"),
            "chars": record.get("prompt_chars", 0),
        }
    return list(by_task.values())


def _signal_from_handoff(record: dict[str, Any]) -> dict[str, Any]:
    raw_signal = record.get("signal")
    signal: dict[str, Any] = raw_signal if isinstance(raw_signal, dict) else {}
    raw_fields = signal.get("fields")
    fields: dict[str, Any] = raw_fields if isinstance(raw_fields, dict) else {}
    role = fields.get("Role") or record.get("subagent_type") or ""
    evidence_refs = _as_evidence_refs(fields.get("EvidenceRefs"))
    evidence_state, self_attestation_only = _signal_state(fields, evidence_refs)
    return {
        "schemaVersion": str(_signal_meta_value(signal, fields, "schemaVersion") or "command-room.evidence-signal/v1"),
        "signalId": str(_signal_meta_value(signal, fields, "signalId") or record.get("task_id") or ""),
        "laneId": str(record.get("task_id") or ""),
        "role": str(role),
        "claim": _truncate(str(fields.get("Claim") or "")),
        "evidenceRefs": evidence_refs,
        "evidenceState": evidence_state,
        "selfAttestationOnly": self_attestation_only,
        "unknownStale": _as_evidence_refs(fields.get("Unknown/Stale")),
        "conflicts": _as_evidence_refs(fields.get("Conflicts")),
        "redlineTouched": _truthy(fields.get("RedlineTouched")) or evidence_state == "REDLINE",
        "recommendedDecision": str(fields.get("RecommendedDecision") or "").strip().upper(),
        "nextAction": _truncate(str(fields.get("NextAction") or "")),
        "valid": bool(signal.get("valid")),
        "missing": signal.get("missing") if isinstance(signal.get("missing"), list) else [],
        "status": record.get("status"),
        "outputRef": {
            "sha256": record.get("result_sha256"),
            "chars": record.get("result_chars", 0),
        },
        "usage": record.get("usage"),
    }


def signals_from_handoffs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_signal_from_handoff(record) for record in records if record.get("status") == "completed"]


def _event_from_handoff_action_result(record: dict[str, Any]) -> dict[str, Any] | None:
    action_result = record.get("action_result")
    if not isinstance(action_result, dict):
        return None
    return {
        "type": f"task_{record.get('status') or 'terminal'}",
        "task_id": record.get("task_id"),
        "action_result": action_result,
    }


def round_context_signals_from_handoffs(goal: str | None, records: list[dict[str, Any]]) -> dict[str, object] | None:
    """Create opt-in RoundContext signals when terminal task action_results exist."""
    round_ = create_round_context(goal or "command-room task round")
    saw_action_result = False
    for record in records:
        event = _event_from_handoff_action_result(record)
        if event is None:
            continue
        saw_action_result = True
        round_ = record_action_result_from_event(round_, event)
    if not saw_action_result:
        return None
    return _json_safe(round_context_signals(round_).as_dict())


def _independent_challenge_rationale_recorded(text: str | None) -> bool:
    sections = _extract_round_card_sections(text)
    haystack = sections.get("opposition") or text or ""
    required = [
        "Not dispatched because",
        "Risk class",
        "Evidence basis",
        "No permission expansion",
        "No PASS from worker self-claim",
    ]
    return all(label.lower() in haystack.lower() for label in required)


def _has_pass_evidence(signals: list[dict[str, Any]], sections: dict[str, str]) -> bool:
    if any(signal.get("valid") and signal.get("evidenceState") == "SUPPORTED" and not signal.get("selfAttestationOnly") and _has_strong_evidence_ref(_as_evidence_refs(signal.get("evidenceRefs"))) for signal in signals):
        return True
    evidence = sections.get("evidence", "").strip().lower()
    if not evidence or evidence in {"none", "无", "n/a", "null"}:
        return False
    weak_markers = [
        "worker 自述",
        "worker says done",
        "self-claim",
        "self claim",
        "summary only",
        "task completed",
        "task done",
        "outputref only",
        "outputref-only",
        "output ref only",
        "无证据",
        "no refs",
        "no evidence",
    ]
    return _STRONG_EVIDENCE_RE.search(evidence) is not None and not any(marker in evidence for marker in weak_markers)


def evaluate_decision_signals(final_text: str | None, signals: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize readiness/evidence/risk signals for the visible decision.

    This is working-memory metadata for the lead AI/Command Room. It is not a
    program-level gate, PASS/FAIL result, automatic rework trigger, or default
    requirement to dispatch opposition.
    """
    verdict_line, model_decision = extract_verdict(final_text)
    sections = _extract_round_card_sections(final_text)
    decision = model_decision
    reasons: list[str] = []

    opposition_signals = [signal for signal in signals if str(signal.get("role") or "").lower() == "opposition"]
    valid_opposition = [signal for signal in opposition_signals if signal.get("valid")]
    blocking_opposition = [signal for signal in valid_opposition if str(signal.get("recommendedDecision") or "").upper() in _BLOCKING_DECISIONS]
    redline_signals = [signal for signal in signals if signal.get("redlineTouched") or signal.get("evidenceState") == "REDLINE"]
    stale_signals = [signal for signal in signals if signal.get("evidenceState") in _STALE_STATES or signal.get("selfAttestationOnly")]

    if blocking_opposition:
        reasons.append("Risk signal: opposition recommends confirmation or more information")

    if redline_signals:
        reasons.append("Risk signal: at least one signal touched a redline")

    if stale_signals:
        reasons.append("Evidence signal: STALE/CONFLICTED or self-attestation-only evidence present")

    if decision == "PASS" and not _has_pass_evidence(signals, sections):
        reasons.append("Evidence signal: concrete refs are not visible; available refs look like worker self-claims or summaries")

    if decision == "PASS" and not valid_opposition and _independent_challenge_rationale_recorded(final_text):
        reasons.append("Readiness signal: independent challenge was not used and the lead AI recorded its rationale")

    if not verdict_line:
        reasons.append("Readiness signal: no explicit Verdict line was found")

    return {
        "decision": decision,
        "modelDecision": model_decision,
        "verdictLine": verdict_line,
        "gated": False,
        "reasons": reasons,
    }


def evaluate_verdict_gate(final_text: str | None, signals: list[dict[str, Any]]) -> dict[str, Any]:
    """Deprecated compatibility alias for :func:`evaluate_decision_signals`."""

    return evaluate_decision_signals(final_text, signals)


def _round_file(thread_id: str, user_id: str | None, base_dir: Path | None = None) -> Path:
    if base_dir is not None:
        return base_dir / "command_room_rounds.jsonl"
    return get_paths().thread_dir(thread_id, user_id=user_id) / "audit" / "command_room_rounds.jsonl"


def record_command_room_round(
    *,
    thread_id: str,
    agent_name: str | None,
    user_id: str | None,
    final_text: str | None,
    user_message: str | None = None,
    run_id: str | None = None,
    usage: dict[str, int] | None = None,
    source: str = "unknown",
    audit_records: list[dict[str, Any]] | None = None,
    base_dir: Path | None = None,
) -> Path | None:
    """Append a minimal RoundRecord for command-room runs.

    Returns the ledger path, or ``None`` for non-command-room agents.
    """
    if agent_name != "command-room":
        return None

    handoff_path: Path | None = None
    if audit_records is None:
        handoff_path, audit_records = _load_handoff_records(thread_id, user_id)
    if run_id is not None:
        audit_records = [record for record in audit_records if record.get("run_id") == run_id]

    sections = _extract_round_card_sections(final_text)
    signals = signals_from_handoffs(audit_records)
    round_signals = round_context_signals_from_handoffs(sections.get("goal") or user_message, audit_records)
    decision_signals = evaluate_decision_signals(final_text, signals)
    path = _round_file(thread_id, user_id, base_dir=base_dir)
    record = {
        "version": 1,
        "roundId": f"round-{uuid.uuid4().hex}",
        "threadId": thread_id,
        "runId": run_id,
        "agentName": agent_name,
        "source": source,
        "visibility": "internal_audit",
        "hide_from_ui": True,
        "timestamp": datetime.now(UTC).isoformat(),
        "intentSeed": _text_fingerprint(user_message),
        "goalHypothesis": sections.get("goal", ""),
        "boundary": sections.get("boundary", ""),
        "capabilityRelease": {
            "subagentsDispatched": bool(audit_records),
            "writeAllowed": False,
        },
        "evidenceStandard": "Readiness benefits from concrete EvidenceRefs/outputRefs; worker self-claims and summaries are weak evidence signals.",
        "dispatchPlan": _dispatch_plan_from_handoffs(audit_records),
        "signals": signals,
        "decisionSignals": decision_signals,
        "readinessSignals": decision_signals,
        "roundContextSignals": round_signals,
        "roundContextAvailable": bool(round_signals),
        "roundContextReason": "command-room task action_result observed" if round_signals else "ordinary/no-task path; no round context signals observed",
        "roundRequired": bool(round_signals),  # deprecated/internal alias: not an auto-return or hard requirement
        "roundRequiredReason": "command-room task action_result observed" if round_signals else "ordinary/no-task path; round not forced",  # deprecated/internal alias
        "verdict": decision_signals,  # deprecated/internal alias for decisionSignals
        "nextRoundContract": {
            "nextGoal": sections.get("next", ""),
            "inheritedBoundary": sections.get("boundary", ""),
            "evidenceSignals": decision_signals["reasons"],
            "requiredEvidence": decision_signals["reasons"],  # deprecated/internal alias
            "allowedDispatch": [],
            "stopBefore": ["new authorization", "production write", "credential/customer/payment exposure"],
            "userConfirmationNeeded": any("redline" in reason.lower() for reason in decision_signals["reasons"]),
        },
        "artifacts": {
            "finalText": _text_fingerprint(final_text),
            "subagentHandoffs": str(handoff_path) if handoff_path is not None else None,
        },
        "usage": usage,
    }

    try:
        record = _json_safe(record)
        with _WRITE_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return path
    except Exception:
        logger.debug("Failed to write command-room RoundRecord", exc_info=True)
        return None


def latest_command_room_round(
    *,
    thread_id: str,
    user_id: str | None,
    base_dir: Path | None = None,
) -> dict[str, Any] | None:
    path = _round_file(thread_id, user_id, base_dir=base_dir)
    if not path.exists():
        return None
    latest: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        latest = json.loads(line)
    return latest

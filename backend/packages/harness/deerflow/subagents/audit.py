"""Append-only audit records for subagent handoffs.

This is a lightweight Naxus-style handoff trail. It intentionally stores
hashes and structured signal fields instead of raw prompts, stdout, or full
worker transcripts.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deerflow.command_room.file_records import append_jsonl_record
from deerflow.config.paths import get_paths

logger = logging.getLogger(__name__)

_FIELD_RE = re.compile(
    r"^(?:[-*+]\s+|\d+[.)]\s+)?[\"'`]*(?:\*\*)?"
    r"(SchemaVersion|SignalId|Role|Claim|EvidenceStrength|EvidenceRefs|EvidenceState|SelfAttestationOnly|"
    r"Unknown/Stale|Conflicts|RedlineTouched|RecommendedDecision|NextAction)"
    r"\s*(?:\*\*)?[\"'`]*\s*[:：]\s*(?:\*\*)?\s*(.*?)\s*,?\s*(?:\*\*)?\s*$",
    re.IGNORECASE,
)
_FIELD_CANONICAL = {
    "schemaversion": "schemaVersion",
    "signalid": "signalId",
    "role": "Role",
    "claim": "Claim",
    "evidencestrength": "EvidenceStrength",
    "evidencerefs": "EvidenceRefs",
    "evidencestate": "evidenceState",
    "selfattestationonly": "selfAttestationOnly",
    "unknown/stale": "Unknown/Stale",
    "conflicts": "Conflicts",
    "redlinetouched": "RedlineTouched",
    "recommendeddecision": "RecommendedDecision",
    "nextaction": "NextAction",
}
_SCHEMA_VERSION = "command-room.evidence-signal/v1"
_EVIDENCE_STATES = {"SUPPORTED", "STALE", "CONFLICTED", "REDLINE"}
_REQUIRED_SIGNAL_FIELDS = ("Role", "Claim", "EvidenceRefs", "RedlineTouched")
_FIELD_VALUE_LIMIT = 1000
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
_REDLINE_RE = re.compile(
    r"STOP_CONFIRM|redline(?:\s+touched)?|permission smuggling|production\s+(?:write|deploy)|"
    r"credential\s+(?:exposure|leak)|customer\s+data|payment\s+data|"
    r"触及红线|权限偷渡|写入生产|生产(?:写入|发布)|凭据(?:暴露|泄露)|客户数据|支付数据",
    re.IGNORECASE,
)
_NEGATED_REDLINE_PREFIX_RE = re.compile(
    r"(?:\bno\b|\bwithout\b|\bdid\s+not\b|\bdoes\s+not\b|\bnot\b)\s+[^.!?\n]{0,80}$|"
    r"(?:未|没有|不涉及|不会|禁止)[^。！？\n]{0,40}$",
    re.IGNORECASE,
)
_BLOCKING_DECISION_RE = re.compile(
    r"STOP_CONFIRM|BLOCKED|NEEDS_MORE|不能\s*PASS|不可\s*PASS|不应\s*PASS|不足以\s*PASS|"
    r"证据缺口|缺少.{0,24}(?:证据|测试输出|文件引用)|missing.{0,24}(?:evidence|test output|file ref)|"
    r"worker\s*self|self-claim|worker\s*自证|权限偷渡|permission smuggling",
    re.IGNORECASE,
)

_HANDOFF_PACKET_FIELD_RE = re.compile(
    r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?(?:\*\*)?"
    r"(Source Role|Source|Sender|From|Target Role|Target|Receiver|To|Goal|"
    r"Task(?:/Question| or Question)?|Question|Context|Inherited Context|Current Context|"
    r"Required Inputs|Inputs|Boundary Status|Boundary|Forbidden|Expected(?: Evidence| Output)?|"
    r"Evidence(?: ?Refs?)?|EvidenceRefs|Evidence(?: ?Strength)?|EvidenceStrength|Output(?: ?Refs?)?|OutputRefs|"
    r"Handoff File|HandoffFile|Handoff Path|HandoffPath|Artifact(?: ?Refs?)?|ArtifactRefs|Artifacts|"
    r"Recommended(?: Next)? Decision|RecommendedDecision|Next Decision|Stop(?: Conditions?)?|"
    r"Failure Conditions|Escalation|Capabilities|Tools|Model|Skill)"
    r"(?:\*\*)?\s*[:：]\s*(.*)$",
    re.IGNORECASE,
)
_HANDOFF_PACKET_KEYS = {
    "source role": "sourceRole",
    "source": "sourceRole",
    "sender": "sourceRole",
    "from": "sourceRole",
    "target role": "targetRole",
    "target": "targetRole",
    "receiver": "targetRole",
    "to": "targetRole",
    "goal": "goal",
    "task": "goal",
    "task/question": "taskOrQuestion",
    "task or question": "taskOrQuestion",
    "question": "taskOrQuestion",
    "context": "context",
    "inherited context": "context",
    "current context": "context",
    "required inputs": "requiredInputs",
    "inputs": "requiredInputs",
    "boundary status": "boundaryStatus",
    "boundary": "boundary",
    "forbidden": "boundary",
    "expected": "expectedEvidence",
    "expected evidence": "expectedEvidence",
    "expected output": "expectedOutput",
    "evidence": "expectedEvidence",
    "evidence refs": "evidenceRefs",
    "evidence ref": "evidenceRefs",
    "evidencerefs": "evidenceRefs",
    "evidence strength": "evidenceStrength",
    "evidencestrength": "evidenceStrength",
    "output refs": "outputRefs",
    "output ref": "outputRefs",
    "outputrefs": "outputRefs",
    "handoff file": "handoffFile",
    "handofffile": "handoffFile",
    "handoff path": "handoffFile",
    "handoffpath": "handoffFile",
    "artifact": "artifactRefs",
    "artifacts": "artifactRefs",
    "artifact refs": "artifactRefs",
    "artifact ref": "artifactRefs",
    "artifactrefs": "artifactRefs",
    "recommended decision": "recommendedNextDecision",
    "recommended next decision": "recommendedNextDecision",
    "recommendeddecision": "recommendedNextDecision",
    "next decision": "recommendedNextDecision",
    "stop": "stopConditions",
    "stop conditions": "stopConditions",
    "stop condition": "stopConditions",
    "failure conditions": "stopConditions",
    "escalation": "stopConditions",
    "capabilities": "releasedCapabilities",
    "tools": "releasedCapabilities",
    "model": "releasedCapabilities",
    "skill": "releasedCapabilities",
}
_HANDOFF_PACKET_REPLACE_KEYS = {"sourceRole", "targetRole", "taskOrQuestion", "evidenceStrength", "handoffFile", "boundaryStatus", "recommendedNextDecision"}
_HANDOFF_PACKET_LIMIT = 500
_SAFE_ACTION_RESULT_KEYS = {
    "action_id",
    "description",
    "status",
    "evidence_refs",
    "output_ref",
    "risks",
    "conflicts",
    "open_questions",
}


def _sha256_text(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _compact_action_result(action_result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not action_result:
        return None
    compact = {key: action_result[key] for key in _SAFE_ACTION_RESULT_KEYS if key in action_result}
    for key in ("summary", "error"):
        value = action_result.get(key)
        if value is not None:
            text = str(value)
            compact[f"{key}_sha256"] = _sha256_text(text)
            compact[f"{key}_chars"] = len(text)
    return compact


def _truncate(value: str, limit: int = _FIELD_VALUE_LIMIT) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}... ({len(value)} chars)"


def _clean_field_value(value: str) -> str:
    cleaned = value.strip().rstrip(",").strip()
    if len(cleaned) >= 2 and cleaned[:1] == cleaned[-1:] and cleaned[:1] in {"'", '"', "`"}:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _first_meaningful_line(text: str | None) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip(" -\t")
        if stripped:
            return stripped
    return ""


def _split_evidence_refs(value: Any) -> list[str]:
    if isinstance(value, list):
        candidates = [str(item) for item in value]
    else:
        candidates = re.split(r"[;\n]+", str(value or ""))
    return [_clean_field_value(item.strip(" -*\t")) for item in candidates if _clean_field_value(item.strip(" -*\t"))]


def _has_strong_evidence_ref(refs: list[str]) -> bool:
    return any(_STRONG_EVIDENCE_RE.search(ref) and not _WEAK_EVIDENCE_RE.search(ref) for ref in refs)


def _is_self_attestation_only(refs: list[str]) -> bool:
    if not refs:
        return True
    return not _has_strong_evidence_ref(refs) and all(_WEAK_EVIDENCE_RE.search(ref) for ref in refs)


def _truthy_text(value: Any) -> bool:
    return str(value or "").strip().lower() in {"true", "yes", "y", "1", "是", "触及", "touched"}


def _nonempty_non_none(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(text) and text not in {"none", "no", "n/a", "null", "无"}


def _redline_touched(text: str | None) -> bool:
    value = text or ""
    for match in _REDLINE_RE.finditer(value):
        prefix = value[max(0, match.start() - 100) : match.start()]
        if not _NEGATED_REDLINE_PREFIX_RE.search(prefix):
            return True
    return False


def _classify_evidence_state(fields: dict[str, Any]) -> tuple[str, bool]:
    refs = _split_evidence_refs(fields.get("EvidenceRefs"))
    redline = _truthy_text(fields.get("RedlineTouched"))
    self_attestation_only = _is_self_attestation_only(refs)
    if redline:
        return "REDLINE", self_attestation_only
    if self_attestation_only:
        return "STALE", True
    if _nonempty_non_none(fields.get("Unknown/Stale")) and not _has_strong_evidence_ref(refs):
        return "STALE", self_attestation_only
    if _nonempty_non_none(fields.get("Conflicts")):
        return "CONFLICTED", self_attestation_only
    return "SUPPORTED", False


def _normalize_evidence_state(value: Any, fallback: str) -> str:
    state = str(value or "").strip().upper()
    return state if state in _EVIDENCE_STATES else fallback


def _infer_recommended_decision(fields: dict[str, str], result: str | None) -> str:
    haystack = "\n".join(
        str(value)
        for value in (
            result,
            fields.get("EvidenceRefs"),
            fields.get("Unknown/Stale"),
            fields.get("Conflicts"),
            fields.get("RedlineTouched"),
        )
        if value
    )
    upper = haystack.upper()
    if "STOP_CONFIRM" in upper or fields.get("RedlineTouched", "").strip().lower() in {"true", "yes", "是"}:
        return "STOP_CONFIRM"
    if "BLOCKED" in upper:
        return "BLOCKED"
    if "NEEDS_MORE" in upper or _BLOCKING_DECISION_RE.search(haystack):
        return "NEEDS_MORE"
    return ""


def _normalize_evidence_signal(
    signal: dict[str, Any],
    *,
    subagent_type: str,
    description: str,
    result: str | None,
    task_id: str,
    action_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recover protocol fields that are already available as lane metadata.

    Worker text is intentionally allowed to be less form-like. The audit record
    may derive mechanical fields such as Role from dispatch metadata while still
    preserving the raw result only by hash.
    """
    fields = dict(signal.get("fields") if isinstance(signal.get("fields"), dict) else {})
    derived: list[str] = []

    if not fields.get("Role") and subagent_type:
        fields["Role"] = _truncate(subagent_type)
        derived.append("Role")

    if not fields.get("Claim"):
        claim_source = fields.get("RecommendedDecision") or description
        claim = _first_meaningful_line(claim_source)
        if claim:
            fields["Claim"] = _truncate(claim)
            derived.append("Claim")

    observed_refs = _split_evidence_refs((action_result or {}).get("evidence_refs"))
    if observed_refs:
        fields["EvidenceRefs"] = _truncate("\n".join(observed_refs))
        derived.append("EvidenceRefs")
    elif not fields.get("EvidenceRefs") and result:
        fields["EvidenceRefs"] = f"worker-output:{task_id}"
        derived.append("EvidenceRefs")

    if not fields.get("RedlineTouched"):
        fields["RedlineTouched"] = "true" if _redline_touched(result) else "false"
        derived.append("RedlineTouched")

    if not fields.get("RecommendedDecision"):
        decision = _infer_recommended_decision(fields, result)
        if decision:
            fields["RecommendedDecision"] = decision
            derived.append("RecommendedDecision")

    inferred_state, inferred_self_attestation = _classify_evidence_state(fields)
    fields["schemaVersion"] = fields.get("schemaVersion") or _SCHEMA_VERSION
    signal_seed = f"{task_id}:{subagent_type}:{fields.get('Claim', '')}"
    fields["signalId"] = fields.get("signalId") or f"sig-{hashlib.sha256(signal_seed.encode('utf-8')).hexdigest()[:16]}"
    fields["evidenceState"] = _normalize_evidence_state(fields.get("evidenceState"), inferred_state)
    fields["selfAttestationOnly"] = bool(_truthy_text(fields.get("selfAttestationOnly")) or inferred_self_attestation)

    missing = [field for field in _REQUIRED_SIGNAL_FIELDS if not fields.get(field)]
    normalized = dict(signal)
    normalized["fields"] = fields
    normalized["missing"] = missing
    normalized["valid"] = not missing
    normalized["schemaVersion"] = fields["schemaVersion"]
    normalized["signalId"] = fields["signalId"]
    normalized["evidenceState"] = fields["evidenceState"]
    normalized["selfAttestationOnly"] = fields["selfAttestationOnly"]
    if derived:
        normalized["derived"] = sorted(set(derived))
    return normalized


def extract_evidence_signal(text: str | None) -> dict[str, Any]:
    """Extract the Evidence Signal fields from a subagent result."""
    if not text:
        return {"valid": False, "fields": {}, "missing": list(_REQUIRED_SIGNAL_FIELDS)}

    fields: dict[str, str] = {}
    current_field: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        match = _FIELD_RE.match(stripped)
        if match:
            raw_name, raw_value = match.groups()
            canonical = _FIELD_CANONICAL[raw_name.lower()]
            fields[canonical] = _truncate(_clean_field_value(raw_value))
            current_field = canonical
            continue

        if current_field and (line[:1].isspace() or stripped.startswith(("-", "*"))):
            fields[current_field] = _truncate(f"{fields[current_field]}\n{stripped}".strip())

    missing = [field for field in _REQUIRED_SIGNAL_FIELDS if not fields.get(field)]
    return {"valid": not missing, "fields": fields, "missing": missing}


def extract_handoff_packet(prompt: str | None, *, description: str = "", subagent_type: str = "") -> dict[str, Any]:
    """Extract compact, mechanical handoff signals from a worker prompt.

    Raw prompts stay out of audit. These fields are best-effort handoff memory
    for comparing a worker result with the packet that produced it; they are not
    runtime evidence and must not upgrade worker self-claims.
    """
    packet: dict[str, Any] = {
        "sourceRole": "command-room",
        "targetRole": _truncate(subagent_type.strip(), _HANDOFF_PACKET_LIMIT),
        "goal": _truncate(description.strip() or _first_meaningful_line(prompt), _HANDOFF_PACKET_LIMIT),
        "taskOrQuestion": _truncate(description.strip() or _first_meaningful_line(prompt), _HANDOFF_PACKET_LIMIT),
        "context": "",
        "requiredInputs": "",
        "boundary": "",
        "boundaryStatus": "",
        "expectedEvidence": "",
        "expectedOutput": "",
        "evidenceRefs": "",
        "evidenceStrength": "",
        "outputRefs": "",
        "handoffFile": "",
        "artifactRefs": "",
        "recommendedNextDecision": "",
        "stopConditions": "",
        "releasedCapabilities": _truncate(subagent_type.strip(), _HANDOFF_PACKET_LIMIT),
    }
    current_key: str | None = None
    for line in (prompt or "").splitlines():
        match = _HANDOFF_PACKET_FIELD_RE.match(line)
        if match:
            raw_key, raw_value = match.groups()
            current_key = _HANDOFF_PACKET_KEYS.get(raw_key.lower())
            if current_key:
                value = _clean_field_value(raw_value)
                if value:
                    if current_key in _HANDOFF_PACKET_REPLACE_KEYS:
                        packet[current_key] = _truncate(value, _HANDOFF_PACKET_LIMIT)
                    else:
                        existing = packet.get(current_key, "")
                        packet[current_key] = _truncate(f"{existing}; {value}" if existing else value, _HANDOFF_PACKET_LIMIT)
            continue
        if current_key and (line[:1].isspace() or line.strip().startswith(("-", "*"))):
            value = line.strip(" -*\t")
            if value:
                existing = packet.get(current_key, "")
                packet[current_key] = _truncate(f"{existing}; {value}" if existing else value, _HANDOFF_PACKET_LIMIT)
    present = [key for key, value in packet.items() if value]
    packet["present"] = present
    return packet


def _audit_file(thread_id: str | None, user_id: str | None, base_dir: Path | None = None) -> Path:
    if base_dir is not None:
        return base_dir / "subagent_handoffs.jsonl"

    paths = get_paths()
    if thread_id:
        try:
            return paths.thread_dir(thread_id, user_id=user_id) / "audit" / "subagent_handoffs.jsonl"
        except ValueError:
            logger.debug("Unsafe thread/user id for subagent audit path; falling back to global audit file", exc_info=True)
    return paths.base_dir / "audit" / "subagent_handoffs.jsonl"


def record_subagent_handoff(
    *,
    thread_id: str | None,
    run_id: str | None,
    task_id: str,
    trace_id: str | None,
    user_id: str | None,
    subagent_type: str,
    description: str,
    prompt: str,
    status: str,
    result: str | None = None,
    error: str | None = None,
    usage: dict[str, int] | None = None,
    action_result: dict[str, Any] | None = None,
    base_dir: Path | None = None,
) -> Path | None:
    """Append a compact handoff audit record.

    The record deliberately omits raw prompt/result/error bodies. The full
    conversation and tool messages remain available through the configured run
    event store when that store is enabled.
    """
    try:
        path = _audit_file(thread_id, user_id, base_dir=base_dir)
        signal = _normalize_evidence_signal(
            extract_evidence_signal(result),
            subagent_type=subagent_type,
            description=description,
            result=result,
            task_id=task_id,
            action_result=action_result,
        )
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "thread_id": thread_id or "unknown",
            "run_id": run_id,
            "task_id": task_id,
            "trace_id": trace_id,
            "user_id": user_id,
            "subagent_type": subagent_type,
            "description": description,
            "status": status,
            "prompt_sha256": _sha256_text(prompt),
            "prompt_chars": len(prompt),
            "handoff_packet": extract_handoff_packet(prompt, description=description, subagent_type=subagent_type),
            "output_handoff_packet": extract_handoff_packet(result, description=description, subagent_type=subagent_type) if result else None,
            "result_sha256": _sha256_text(result),
            "result_chars": len(result or ""),
            "error_sha256": _sha256_text(error),
            "error_chars": len(error or ""),
            "usage": usage,
            "signal": signal,
            "action_result": _compact_action_result(action_result),
        }
        return append_jsonl_record(path, record)
    except Exception:
        logger.debug("Failed to write subagent handoff audit record", exc_info=True)
        return None

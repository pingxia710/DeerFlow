"""Append-only objective audit records for subagent handoffs.

Records preserve dispatch metadata and hashes without retaining raw prompt or
worker text. They never infer evidence strength, a verdict, or a next action
from a subagent's natural-language result.
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

_FIELD_VALUE_LIMIT = 1000

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
            "result_sha256": _sha256_text(result),
            "result_chars": len(result or ""),
            "error_sha256": _sha256_text(error),
            "error_chars": len(error or ""),
            "usage": usage,
            "action_result": _compact_action_result(action_result),
        }
        return append_jsonl_record(path, record)
    except Exception:
        logger.debug("Failed to write subagent handoff audit record", exc_info=True)
        return None

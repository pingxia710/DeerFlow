"""AI-authored account update proposals and Chair decisions."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from deerflow.config.paths import get_paths

AccountType = Literal["goal", "boundary", "decision", "evidence", "debt", "learning"]
AccountDecisionValue = Literal["adopt", "revise", "defer", "reject"]

ACCOUNT_TYPES = frozenset({"goal", "boundary", "decision", "evidence", "debt", "learning"})
ACCOUNT_DECISIONS = frozenset({"adopt", "revise", "defer", "reject"})
_TEXT_LIMIT = 2000
_REF_LIMIT = 20
_WRITE_LOCK = threading.Lock()


@dataclass
class AccountUpdateProposal:
    proposal_id: str
    thread_id: str
    run_id: str
    round_id: str | None
    task_id: str | None
    proposed_by_role: str
    account_type: AccountType
    proposed_change: str
    rationale: str
    evidence_refs: list[str]
    quality_signal_refs: list[str]
    review_invocation_refs: list[str]
    target_role: str
    created_at: str
    ai_authored: bool = True
    schema_version: int = 1
    record_type: str = "proposal"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AccountDecision:
    decision_id: str
    proposal_id: str
    thread_id: str
    run_id: str
    decided_by_role: str
    decision: AccountDecisionValue
    rationale: str
    revised_change: str | None
    evidence_refs: list[str]
    created_at: str
    ai_authored: bool = True
    schema_version: int = 1
    record_type: str = "decision"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _clip(value: Any, limit: int = _TEXT_LIMIT) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _clean_optional(value: Any, limit: int = _TEXT_LIMIT) -> str | None:
    text = _clip(value, limit)
    return text or None


def _clean_list(value: Any, *, limit: int = _REF_LIMIT) -> list[str]:
    if isinstance(value, str):
        raw_items: list[Any] = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    items: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = _clip(item)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
        if len(items) >= limit:
            break
    return items


def _account_type(value: Any) -> AccountType:
    account_type = str(value or "").strip().lower().replace("-", "_")
    if account_type not in ACCOUNT_TYPES:
        allowed = ", ".join(sorted(ACCOUNT_TYPES))
        raise ValueError(f"Unsupported account_type: {account_type or '<empty>'}; expected one of {allowed}")
    return account_type  # type: ignore[return-value]


def _decision(value: Any) -> AccountDecisionValue:
    decision = str(value or "").strip().lower().replace("-", "_")
    if decision not in ACCOUNT_DECISIONS:
        allowed = ", ".join(sorted(ACCOUNT_DECISIONS))
        raise ValueError(f"Unsupported account decision: {decision or '<empty>'}; expected one of {allowed}")
    return decision  # type: ignore[return-value]


def build_account_update_proposal(
    *,
    thread_id: str,
    run_id: str,
    proposed_by_role: str,
    account_type: str,
    proposed_change: str,
    rationale: str,
    round_id: str | None = None,
    task_id: str | None = None,
    evidence_refs: list[str] | None = None,
    quality_signal_refs: list[str] | None = None,
    review_invocation_refs: list[str] | None = None,
    target_role: str = "Chair",
    proposal_id: str | None = None,
    created_at: str | None = None,
) -> AccountUpdateProposal:
    proposer = _clip(proposed_by_role, 64).lower()
    if not proposer:
        raise ValueError("Account update proposal proposed_by_role is required")
    change = _clip(proposed_change)
    if not change:
        raise ValueError("Account update proposal proposed_change is required")
    reason = _clip(rationale)
    if not reason:
        raise ValueError("Account update proposal rationale is required")
    return AccountUpdateProposal(
        proposal_id=_clip(proposal_id, 128) or f"account-proposal-{uuid.uuid4().hex}",
        thread_id=thread_id,
        run_id=run_id,
        round_id=_clean_optional(round_id, 128),
        task_id=_clean_optional(task_id, 128),
        proposed_by_role=proposer,
        account_type=_account_type(account_type),
        proposed_change=change,
        rationale=reason,
        evidence_refs=_clean_list(evidence_refs),
        quality_signal_refs=_clean_list(quality_signal_refs),
        review_invocation_refs=_clean_list(review_invocation_refs),
        target_role=_clip(target_role, 64) or "Chair",
        created_at=created_at or _now_iso(),
    )


def account_update_proposal_from_dict(data: dict[str, Any]) -> AccountUpdateProposal:
    return build_account_update_proposal(
        proposal_id=data.get("proposal_id"),
        thread_id=str(data.get("thread_id") or ""),
        run_id=str(data.get("run_id") or ""),
        round_id=data.get("round_id"),
        task_id=data.get("task_id"),
        proposed_by_role=str(data.get("proposed_by_role") or ""),
        account_type=str(data.get("account_type") or ""),
        proposed_change=str(data.get("proposed_change") or ""),
        rationale=str(data.get("rationale") or ""),
        evidence_refs=_clean_list(data.get("evidence_refs")),
        quality_signal_refs=_clean_list(data.get("quality_signal_refs")),
        review_invocation_refs=_clean_list(data.get("review_invocation_refs")),
        target_role=str(data.get("target_role") or "Chair"),
        created_at=str(data.get("created_at") or "") or None,
    )


def build_account_decision(
    *,
    proposal_id: str,
    thread_id: str,
    run_id: str,
    decision: str,
    rationale: str,
    decided_by_role: str = "chair",
    revised_change: str | None = None,
    evidence_refs: list[str] | None = None,
    decision_id: str | None = None,
    created_at: str | None = None,
) -> AccountDecision:
    proposal = _clip(proposal_id, 128)
    if not proposal:
        raise ValueError("Account decision proposal_id is required")
    decider = _clip(decided_by_role, 64).lower() or "chair"
    reason = _clip(rationale)
    if not reason:
        raise ValueError("Account decision rationale is required")
    return AccountDecision(
        decision_id=_clip(decision_id, 128) or f"account-decision-{uuid.uuid4().hex}",
        proposal_id=proposal,
        thread_id=thread_id,
        run_id=run_id,
        decided_by_role=decider,
        decision=_decision(decision),
        rationale=reason,
        revised_change=_clean_optional(revised_change),
        evidence_refs=_clean_list(evidence_refs),
        created_at=created_at or _now_iso(),
    )


def account_decision_from_dict(data: dict[str, Any]) -> AccountDecision:
    return build_account_decision(
        decision_id=data.get("decision_id"),
        proposal_id=str(data.get("proposal_id") or ""),
        thread_id=str(data.get("thread_id") or ""),
        run_id=str(data.get("run_id") or ""),
        decided_by_role=str(data.get("decided_by_role") or "chair"),
        decision=str(data.get("decision") or ""),
        rationale=str(data.get("rationale") or ""),
        revised_change=data.get("revised_change") if isinstance(data.get("revised_change"), str) else None,
        evidence_refs=_clean_list(data.get("evidence_refs")),
        created_at=str(data.get("created_at") or "") or None,
    )


def _account_ledger_file(thread_id: str, user_id: str | None, base_dir: Path | None = None) -> Path:
    if base_dir is not None:
        return base_dir / "account_ledger.jsonl"
    return get_paths().thread_dir(thread_id, user_id=user_id) / "audit" / "account_ledger.jsonl"


def _append_record(row: dict[str, Any], *, thread_id: str, user_id: str | None = None, base_dir: Path | None = None) -> Path:
    path = _account_ledger_file(thread_id, user_id, base_dir=base_dir)
    with _WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def record_account_update_proposal(proposal: AccountUpdateProposal, *, user_id: str | None = None, base_dir: Path | None = None) -> Path:
    return _append_record(proposal.as_dict(), thread_id=proposal.thread_id, user_id=user_id, base_dir=base_dir)


def record_account_decision(decision: AccountDecision, *, user_id: str | None = None, base_dir: Path | None = None) -> Path:
    return _append_record(decision.as_dict(), thread_id=decision.thread_id, user_id=user_id, base_dir=base_dir)


def _list_account_records(
    *,
    thread_id: str,
    user_id: str | None,
    record_type: str,
    run_id: str | None = None,
    round_id: str | None = None,
    task_id: str | None = None,
    limit: int = 50,
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    path = _account_ledger_file(thread_id, user_id, base_dir=base_dir)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("record_type") != record_type:
            continue
        if row.get("thread_id") != thread_id:
            continue
        if run_id is not None and row.get("run_id") != run_id:
            continue
        if round_id is not None and row.get("round_id") != round_id:
            continue
        if task_id is not None and row.get("task_id") != task_id:
            continue
        rows.append(row)
    return rows[-limit:]


def list_account_proposals(
    *,
    thread_id: str,
    user_id: str | None,
    run_id: str | None = None,
    round_id: str | None = None,
    task_id: str | None = None,
    limit: int = 50,
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    return _list_account_records(
        thread_id=thread_id,
        user_id=user_id,
        run_id=run_id,
        round_id=round_id,
        task_id=task_id,
        record_type="proposal",
        limit=limit,
        base_dir=base_dir,
    )


def list_account_decisions(
    *,
    thread_id: str,
    user_id: str | None,
    run_id: str | None = None,
    limit: int = 50,
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    return _list_account_records(
        thread_id=thread_id,
        user_id=user_id,
        run_id=run_id,
        record_type="decision",
        limit=limit,
        base_dir=base_dir,
    )


def compact_account_ledger(proposals: list[dict[str, Any]], decisions: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    proposal_by_id = {str(proposal.get("proposal_id")): proposal for proposal in proposals if proposal.get("proposal_id")}
    entries: list[dict[str, Any]] = []
    for proposal in proposals[-limit:]:
        entries.append(
            {
                "entry": "proposal",
                "account_type": proposal.get("account_type"),
                "proposed_by_role": proposal.get("proposed_by_role"),
                "target_role": proposal.get("target_role") or "Chair",
                "created_at": proposal.get("created_at"),
            }
        )
    for decision in decisions[-limit:]:
        proposal = proposal_by_id.get(str(decision.get("proposal_id"))) or {}
        entries.append(
            {
                "entry": "decision",
                "account_type": proposal.get("account_type"),
                "proposed_by_role": proposal.get("proposed_by_role"),
                "decision": decision.get("decision"),
                "target_role": proposal.get("target_role") or "Chair",
                "created_at": decision.get("created_at"),
            }
        )
    return entries[-limit:]


__all__ = [
    "ACCOUNT_DECISIONS",
    "ACCOUNT_TYPES",
    "AccountDecision",
    "AccountDecisionValue",
    "AccountType",
    "AccountUpdateProposal",
    "account_decision_from_dict",
    "account_update_proposal_from_dict",
    "build_account_decision",
    "build_account_update_proposal",
    "compact_account_ledger",
    "list_account_decisions",
    "list_account_proposals",
    "record_account_decision",
    "record_account_update_proposal",
]

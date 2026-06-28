"""Small, UI-friendly Command Room round contract.

A round is a lightweight working-memory bundle around a bounded goal. It is not
a chat turn and not a workflow gate: within the current goal, boundary, allowed
capabilities, and evidence standard, it records what state moved, what remains
open, what decision signals are visible, and whether the proposed next step stays
inside bounds.

This module is intentionally runtime-agnostic. It does not persist data, call
models, or decide how agents execute work; it only provides a simple testable
shape and a short natural-language summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .evidence import summarize_evidence_refs


class RoundItemStatus(StrEnum):
    """Status for a dispatched subtask/action inside a round."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class RoundAction:
    """A subtask or action dispatched during a round."""

    title: str
    status: RoundItemStatus = RoundItemStatus.PENDING
    assignee: str | None = None
    result_ref: str | None = None

    @property
    def is_done(self) -> bool:
        return self.status == RoundItemStatus.COMPLETED


@dataclass(frozen=True)
class ActionResult:
    """Runtime/adapter-normalized result for a dispatched action/subtask.

    This intentionally mirrors the small shape exposed by task/subagent terminal
    events without coupling the command-room model to the task runtime. It is
    not a required hand-written sub-AI response format; natural text should stay
    in ``summary`` unless observable metadata/tool output supplies evidence refs.
    """

    action_id: str
    description: str = ""
    status: RoundItemStatus = RoundItemStatus.COMPLETED
    summary: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    output_ref: str | None = None
    risks: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def is_done(self) -> bool:
        return self.status == RoundItemStatus.COMPLETED


@dataclass(frozen=True)
class NextRound:
    """Minimal proposal for the next bounded round."""

    proposal: str
    within_current_boundary: bool = True
    needs_user_confirmation: bool = False
    reason: str = ""


@dataclass(frozen=True)
class Round:
    """A simple contract for one bounded state advancement."""

    goal: str
    boundaries: list[str] = field(default_factory=list)
    known_facts: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    allowed_actions: list[str] = field(default_factory=list)
    evidence_standard: list[str] = field(default_factory=list)
    actions: list[RoundAction] = field(default_factory=list)
    action_results: list[ActionResult] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    state_delta: list[str] = field(default_factory=list)
    verdict: str = ""
    next_step: str = ""
    next_round: NextRound | None = None
    needs_user_confirmation: bool = False
    intervention_id: str | None = None

    @property
    def actions_completed(self) -> bool:
        """Whether every dispatched action/subtask has completed."""
        return bool(self.actions) and all(action.is_done for action in self.actions)

    @property
    def has_evidence(self) -> bool:
        """Whether the round has at least one concrete evidence/result reference."""
        return any(ref.strip() for ref in self.evidence_refs)

    def evidence_signals(self) -> dict[str, object]:
        """Return aggregate evidence strength/weakness hints for summaries.

        The result is mechanical signal metadata only: it does not make a
        PASS/FAIL quality verdict, does not mark the round complete, and does
        not trigger rework.
        """
        return summarize_evidence_refs(self.evidence_refs)

    @property
    def has_open_questions(self) -> bool:
        """Whether any explicit question/unresolved item is still open."""
        return any(item.strip() for item in self.open_questions) or any(item.strip() for item in self.unresolved)

    @property
    def has_risks_or_conflicts(self) -> bool:
        """Whether unresolved risks/conflicts prevent conservative completion."""
        return any(item.strip() for item in self.risks) or any(item.strip() for item in self.conflicts)

    def record_action_result(self, result: ActionResult) -> Round:
        """Return a new round with one action/subtask result recorded.

        The merge is intentionally conservative: evidence, open questions, risks,
        conflicts, and errors are carried into round-level working memory. ``output_ref``
        remains attached to the result and is not treated as evidence by itself.
        """
        evidence_refs = _append_unique(self.evidence_refs, result.evidence_refs)
        open_questions = _append_unique(self.open_questions, result.open_questions)
        risks = _append_unique(self.risks, result.risks)
        conflicts = _append_unique(self.conflicts, result.conflicts)
        unresolved = list(self.unresolved)
        if result.error and result.error.strip():
            unresolved = _append_unique(unresolved, [result.error])
        return Round(
            goal=self.goal,
            boundaries=list(self.boundaries),
            known_facts=list(self.known_facts),
            open_questions=open_questions,
            allowed_actions=list(self.allowed_actions),
            evidence_standard=list(self.evidence_standard),
            actions=list(self.actions),
            action_results=[*self.action_results, result],
            evidence_refs=evidence_refs,
            unresolved=unresolved,
            risks=risks,
            conflicts=conflicts,
            state_delta=list(self.state_delta),
            verdict=self.verdict,
            next_step=self.next_step,
            next_round=self.next_round,
            needs_user_confirmation=self.needs_user_confirmation,
            intervention_id=self.intervention_id,
        )

    add_action_result = record_action_result

    @property
    def next_round_is_safe(self) -> bool:
        """Whether the proposed next round appears inside the current boundary."""
        if self.next_round is None:
            return True
        return self.next_round.within_current_boundary and not self.next_round.needs_user_confirmation

    @property
    def is_complete(self) -> bool:
        """Conservative local completion hint.

        This is a mechanical readiness signal for the main AI, not an automatic
        PASS/FAIL or rework decision. The hint stays false without evidence, with
        unresolved/open questions, when user confirmation is needed, or when the
        proposed next round is outside the current boundary.
        """
        return self.has_evidence and not self.has_open_questions and not self.has_risks_or_conflicts and not self.needs_user_confirmation and self.next_round_is_safe


def _clean(items: list[str]) -> list[str]:
    return [item.strip() for item in items if item.strip()]


def _append_unique(existing: list[str], additions: list[str]) -> list[str]:
    merged = list(existing)
    seen = {item.strip() for item in merged if item.strip()}
    for item in additions:
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            merged.append(cleaned)
            seen.add(cleaned)
    return merged


def summarize_round(round_: Round) -> str:
    """Return a short natural Chinese summary for UI/log display."""
    parts = [f"本轮围绕“{round_.goal.strip() or '未说明'}”推进。"]

    deltas = _clean(round_.state_delta)
    if deltas:
        parts.append("推进了" + "；".join(deltas[:2]) + "。")
    elif round_.action_results:
        done_results = [result for result in round_.action_results if result.is_done]
        if done_results:
            label = done_results[0].description or done_results[0].action_id
            parts.append(f"动作“{label}”已完成。")
        else:
            parts.append(f"记录了 {len(round_.action_results)} 个动作结果。")
    elif round_.actions:
        done = sum(1 for action in round_.actions if action.is_done)
        parts.append(f"推进了 {done}/{len(round_.actions)} 项已允许动作。")
    else:
        parts.append("尚未形成明确状态推进。")

    facts = _clean(round_.known_facts)
    evidence = _clean(round_.evidence_refs)
    if evidence:
        basis = evidence[:2]
        if facts:
            basis = facts[:1] + basis
        parts.append("依据是" + "；".join(basis) + "。")
        evidence_summary = round_.evidence_signals()
        strong_count = evidence_summary["strong_count"]
        weak_count = evidence_summary["weak_count"]
        if strong_count:
            parts.append(f"证据信号：{strong_count} 条强引用。")
        elif weak_count:
            parts.append(f"证据信号：{weak_count} 条弱引用，需主 AI 结合上下文判断。")
    else:
        parts.append("目前依据不足，不能判定完成。")

    if round_.verdict.strip():
        parts.append(f"结论：{round_.verdict.strip()}。")

    outputs = _clean([result.output_ref or "" for result in round_.action_results])
    if outputs:
        parts.append("输出见" + "；".join(outputs[:2]) + "。")

    if round_.has_open_questions:
        questions = _clean(round_.open_questions + round_.unresolved)
        parts.append("仍待澄清：" + "；".join(questions[:2]) + "。")

    blockers = _clean(round_.risks + round_.conflicts)
    if blockers:
        parts.append("仍有风险/冲突：" + "；".join(blockers[:2]) + "。")

    if round_.needs_user_confirmation or (round_.next_round is not None and round_.next_round.needs_user_confirmation):
        parts.append("本轮尚未完成，下一步触及红线/授权边界，需要用户确认。")
    elif round_.next_round is not None and not round_.next_round.within_current_boundary:
        parts.append("本轮尚未完成，下一轮建议已越过当前边界，需要先确认边界。")
    elif round_.next_round is not None and round_.next_round.proposal.strip():
        prefix = "本轮尚未完成但可继续自主排查：" if not round_.is_complete else "下一步可继续："
        parts.append(f"{prefix}{round_.next_round.proposal.strip()}。")
    elif round_.next_step.strip():
        prefix = "本轮尚未完成但可继续自主排查：" if not round_.is_complete else "下一步可继续："
        parts.append(f"{prefix}{round_.next_step.strip()}。")

    return "".join(parts)

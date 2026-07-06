"""Runs endpoints — create, stream, wait, cancel.

Implements the LangGraph Platform runs API on top of
:class:`deerflow.agents.runs.RunManager` and
:class:`deerflow.agents.stream_bridge.StreamBridge`.

SSE format is aligned with the LangGraph Platform protocol so that
the ``useStream`` React hook from ``@langchain/langgraph-sdk/react``
works without modification.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import mimetypes
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from langchain_core.messages import BaseMessage
from pydantic import AliasChoices, BaseModel, Field, model_validator

from app.gateway.authz import require_permission
from app.gateway.deps import (
    get_checkpointer,
    get_config,
    get_feedback_repo,
    get_round_state_store,
    get_run_event_store,
    get_run_manager,
    get_run_store,
    get_stream_bridge,
    get_thread_store,
)
from app.gateway.pagination import trim_run_message_page
from app.gateway.path_utils import get_request_storage_user_id, resolve_thread_virtual_path
from app.gateway.services import resolve_thread_run, sse_consumer, start_run, wait_for_run_completion
from app.gateway.utils import sanitize_log_param
from deerflow.capabilities import build_capability_snapshot
from deerflow.command_room.account_ledger import (
    build_account_decision,
    build_account_update_proposal,
    list_account_decisions,
    list_account_proposals,
    record_account_decision,
    record_account_update_proposal,
)
from deerflow.command_room.brief import build_chair_operating_brief
from deerflow.command_room.evidence import normalize_evidence_ref
from deerflow.command_room.pending_handoff import (
    PendingHandoffStatus,
    list_pending_handoffs,
    resolve_pending_handoff,
)
from deerflow.command_room.plan import (
    LaneStatus,
    build_chair_decision,
    build_planned_lane,
    build_round_plan,
    latest_round_plan,
    list_chair_decisions,
    list_planned_lanes,
    list_round_plans,
    record_chair_decision,
    record_planned_lane,
    record_round_plan,
    update_planned_lane_status,
)
from deerflow.command_room.quality import build_quality_signal, list_quality_signals, record_quality_signal
from deerflow.command_room.review import (
    build_review_invocation,
    complete_review_invocation,
    list_review_invocations,
    record_review_invocation,
    review_invocation_from_dict,
)
from deerflow.command_room.role_state import build_role_state, list_role_states, record_role_state
from deerflow.config.app_config import AppConfig
from deerflow.runtime import RunRecord, RunStatus, serialize_channel_values_for_api
from deerflow.runtime.artifacts import build_artifact_index
from deerflow.runtime.runs.schemas import is_inflight_status, is_terminal_status, run_status_value
from deerflow.utils.messages import ORIGINAL_USER_CONTENT_KEY, get_original_user_content_text, message_to_text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/threads", tags=["runs"])
REGENERATE_HISTORY_SCAN_LIMIT = 200
HIDDEN_CONTROL_MESSAGE_NAMES = frozenset({"summary", "loop_warning", "todo_reminder", "todo_completion_reminder"})
INTERNAL_CONTEXT_TAG_RE = re.compile(r"<(uploaded_files|slash_skill_activation)>[\s\S]*?</\1>")
_CANCEL_WAIT_TIMEOUT_SECONDS = 10.0
_RUN_TERMINAL_REASON_ALIASES = {
    "boundary_blocked": "boundary_stopped",
    "lease_expired_recovered": "worker_lost",
    "polling_timed_out": "timeout",
    "rollback_failed_owner_lost": "rollback_failed",
    "timed_out": "timeout",
    "user_cancelled": "cancelled",
}
_WORKER_LOST_ERROR_MARKERS = (
    "gateway restarted before this run reached a durable final state",
    "worker lost",
    "owner lost",
)
_ACTIVE_ARTIFACT_MIME_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
}
_ARTIFACT_HASH_CHUNK_BYTES = 1024 * 1024
_ARTIFACT_TEXT_SAMPLE_BYTES = 8192
_SENSITIVE_RUN_ERROR_MARKERS = (
    "secret",
    "stack trace",
    "traceback",
    "token",
    "api key",
    "password",
)
_PROVIDER_TRANSIENT_RUN_ERROR_MARKERS = (
    "codex api stream ended without response.completed event",
    "codexstreamincompleteerror",
    "response.completed",
)
_PUBLIC_PROVIDER_TRANSIENT_RUN_ERROR = "The configured LLM provider is temporarily unavailable after multiple retries. Please wait a moment and continue the conversation."
_MAX_PUBLIC_RUN_ERROR_CHARS = 200
_RUN_ERROR_EXCEPTION_RE = re.compile(r"^[A-Za-z_][\w.]*?(?:Error|Exception):\s*(?P<message>.+)$")
_RUN_ERROR_TRACEBACK_FRAME_PREFIXES = (
    "Traceback ",
    "File ",
    "During handling of the above exception",
    "The above exception was the direct cause",
    "^",
)
_RUN_EVIDENCE_EVENT_TYPES = [
    "artifact.presented",
    "llm.tool.result",
    "run.error",
    "llm.error",
    "task_completed",
    "task_failed",
    "task_cancelled",
    "task_timed_out",
    "task.completed",
    "task.failed",
    "task.cancelled",
    "task.timed_out",
]
_TERMINAL_ROUND_STATES = frozenset({"closed", "blocked"})
_ACTIVE_TASK_LANE_STATUSES = frozenset({"in_progress", "running", "pending", "executing"})
_TASK_EVENT_PROJECTION_TYPES = frozenset({"task_started", "task_completed", "task_failed", "task_cancelled", "task_timed_out"})
_TASK_EVENT_TERMINAL_STATUS_BY_TYPE = {
    "task_completed": "completed",
    "task_failed": "failed",
    "task_cancelled": "cancelled",
    "task_timed_out": "timed_out",
}
_TASK_LANE_TERMINAL_EVENT_BY_STATUS = {
    "completed": "task_completed",
    "failed": "task_failed",
    "cancelled": "task_cancelled",
    "timed_out": "task_timed_out",
}


async def _bounded_wait_for_cancelled_task(task: asyncio.Task) -> None:
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=_CANCEL_WAIT_TIMEOUT_SECONDS)
    except TimeoutError:
        raise HTTPException(status_code=202, detail="Cancel requested; run did not settle before wait timeout")
    except (asyncio.CancelledError, Exception):
        pass


def compute_run_durations(runs) -> dict[str, int]:
    """Map run_id -> duration in seconds from run timestamps."""
    from datetime import datetime

    durations: dict[str, int] = {}
    for r in runs:
        if r.created_at and r.updated_at:
            try:
                created = datetime.fromisoformat(r.created_at.replace("Z", "+00:00"))
                updated = datetime.fromisoformat(r.updated_at.replace("Z", "+00:00"))
                # Note: updated_at - created_at represents the row's total lifetime,
                # which can slightly overshoot the actual AI turn end if the row is mutated later.
                durations[r.run_id] = int((updated - created).total_seconds())
            except Exception:
                logger.warning("Failed to parse timestamps for run %s", r.run_id, exc_info=True)
    return durations


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RunCreateRequest(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _default_resumable_disconnect(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if data.get("stream_resumable", data.get("streamResumable")) is True and "on_disconnect" not in data and "onDisconnect" not in data:
            return {**data, "on_disconnect": "continue"}
        return data

    assistant_id: str | None = Field(default=None, description="Agent / assistant to use")
    input: dict[str, Any] | None = Field(default=None, description="Graph input (e.g. {messages: [...]})")
    command: dict[str, Any] | None = Field(default=None, description="LangGraph Command")
    metadata: dict[str, Any] | None = Field(default=None, description="Run metadata")
    config: dict[str, Any] | None = Field(default=None, description="RunnableConfig overrides")
    context: dict[str, Any] | None = Field(default=None, description="DeerFlow context overrides (model_name, thinking_enabled, etc.)")
    webhook: str | None = Field(default=None, description="Completion callback URL")
    checkpoint_id: str | None = Field(default=None, description="Resume from checkpoint")
    checkpoint: dict[str, Any] | None = Field(default=None, description="Full checkpoint object")
    interrupt_before: list[str] | Literal["*"] | None = Field(default=None, description="Nodes to interrupt before")
    interrupt_after: list[str] | Literal["*"] | None = Field(default=None, description="Nodes to interrupt after")
    stream_mode: list[str] | str | None = Field(default=None, description="Stream mode(s)")
    stream_subgraphs: bool = Field(default=False, description="Include subgraph events")
    stream_resumable: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("stream_resumable", "streamResumable"),
        description="SSE resumable mode",
    )
    on_disconnect: Literal["cancel", "continue"] = Field(
        default="cancel",
        validation_alias=AliasChoices("on_disconnect", "onDisconnect"),
        description="Behaviour on SSE disconnect",
    )
    on_completion: Literal["delete", "keep"] = Field(default="keep", description="Delete temp thread on completion")
    multitask_strategy: Literal["reject", "rollback", "interrupt", "enqueue"] = Field(default="reject", description="Concurrency strategy")
    after_seconds: float | None = Field(default=None, description="Delayed execution")
    if_not_exists: Literal["reject", "create"] = Field(default="create", description="Thread creation policy")
    feedback_keys: list[str] | None = Field(default=None, description="LangSmith feedback keys")


class RegeneratePrepareRequest(BaseModel):
    message_id: str = Field(..., min_length=1, description="Assistant message id to regenerate")


class RegeneratePrepareResponse(BaseModel):
    input: dict[str, Any]
    checkpoint: dict[str, Any]
    metadata: dict[str, Any]
    target_run_id: str


class RunResponse(BaseModel):
    run_id: str
    thread_id: str
    round_id: str | None = None
    round_state: str | None = None
    assistant_id: str | None = None
    status: str
    terminal_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    kwargs: dict[str, Any] = Field(default_factory=dict)
    multitask_strategy: str = "reject"
    created_at: str = ""
    updated_at: str = ""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    llm_call_count: int = 0
    lead_agent_tokens: int = 0
    subagent_tokens: int = 0
    middleware_tokens: int = 0
    message_count: int = 0


class RoundResponse(BaseModel):
    round_id: str
    thread_id: str
    user_id: str | None = None
    parent_round_id: str | None = None
    current_run_id: str | None = None
    source_goal_run_id: str | None = None
    current_intent: str | None = None
    state: str
    next_action: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    closed_at: str | None = None


class TaskLaneResponse(BaseModel):
    thread_id: str
    run_id: str
    task_id: str
    round_id: str
    user_id: str | None = None
    role: str | None = None
    status: str
    result_ref: str | None = None
    evidence_ref: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    handoff: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""


class RuntimeSnapshotRunMessages(BaseModel):
    run_id: str
    data: list[dict[str, Any]] = Field(default_factory=list)
    has_more: bool = False


class RuntimeSnapshotRecoveredRunResponse(BaseModel):
    run_id: str
    terminal_reason: str | None = None


class RuntimeSnapshotStaleInflightRecoveryResponse(BaseModel):
    recovered: bool = False
    recovered_count: int = 0
    run_ids: list[str] = Field(default_factory=list)
    terminal_reason: str | None = None
    runs: list[RuntimeSnapshotRecoveredRunResponse] = Field(default_factory=list)


class RuntimeSnapshotSelfHealRoundResponse(BaseModel):
    run_id: str
    round_id: str
    state: str


class RuntimeSnapshotSelfHealTaskLaneResponse(BaseModel):
    run_id: str
    round_id: str
    task_id: str
    status: str


class RuntimeSnapshotSelfHealResponse(BaseModel):
    repaired: bool = False
    round_count: int = 0
    task_lane_count: int = 0
    rounds: list[RuntimeSnapshotSelfHealRoundResponse] = Field(default_factory=list)
    task_lanes: list[RuntimeSnapshotSelfHealTaskLaneResponse] = Field(default_factory=list)


class RuntimeSnapshotRecoveryResponse(BaseModel):
    stale_inflight: RuntimeSnapshotStaleInflightRecoveryResponse | None = None
    snapshot_self_heal: RuntimeSnapshotSelfHealResponse | None = None


class ThreadRuntimeSnapshotResponse(BaseModel):
    thread_id: str
    runs: list[RunResponse] = Field(default_factory=list)
    run_messages: list[RuntimeSnapshotRunMessages] = Field(default_factory=list)
    rounds: list[RoundResponse] = Field(default_factory=list)
    task_lanes: list[TaskLaneResponse] = Field(default_factory=list)
    recovery: RuntimeSnapshotRecoveryResponse | None = None


QualityRecommendation = Literal["continue", "needs_more_evidence", "needs_revision", "escalate", "stop"]


class QualitySignalCreateRequest(BaseModel):
    round_id: str | None = None
    task_id: str | None = None
    author_role: str = Field(..., min_length=1, max_length=64)
    recommendation: QualityRecommendation
    rationale: str = Field(..., min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list)
    capability_refs: list[str] = Field(default_factory=list)
    capability_snapshot_version: int | None = None
    target_role: str = Field(default="Chair", min_length=1, max_length=64)


class QualitySignalResponse(BaseModel):
    signal_id: str
    thread_id: str
    run_id: str
    round_id: str | None = None
    task_id: str | None = None
    author_role: str
    recommendation: QualityRecommendation
    rationale: str
    evidence_refs: list[str] = Field(default_factory=list)
    capability_refs: list[str] = Field(default_factory=list)
    capability_snapshot_version: int | None = None
    target_role: str = "Chair"
    created_at: str
    ai_authored: bool = True
    programmatic_decision: bool = False
    quality_verdict: None = None
    auto_rework: bool = False
    schema_version: int = 1


ReviewerRole = Literal["evidence_checker", "opposition", "synthesis_checker", "reviewer"]
ReviewInvocationStatus = Literal["requested", "completed", "cancelled"]


class ReviewInvocationCreateRequest(BaseModel):
    round_id: str | None = None
    task_id: str | None = None
    requested_by_role: str = Field(..., min_length=1, max_length=64)
    reviewer_role: ReviewerRole
    reason: str = Field(..., min_length=1, max_length=4000)
    focus: str = Field(..., min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list)
    handoff_refs: list[str] = Field(default_factory=list)
    quality_signal_refs: list[str] = Field(default_factory=list)
    target_role: str = Field(default="Chair", min_length=1, max_length=64)


class ReviewInvocationCompleteRequest(BaseModel):
    result_summary: str = Field(..., min_length=1, max_length=4000)
    result_evidence_refs: list[str] = Field(default_factory=list)


class ReviewInvocationResponse(BaseModel):
    invocation_id: str
    thread_id: str
    run_id: str
    round_id: str | None = None
    task_id: str | None = None
    requested_by_role: str
    reviewer_role: ReviewerRole
    reason: str
    focus: str
    evidence_refs: list[str] = Field(default_factory=list)
    handoff_refs: list[str] = Field(default_factory=list)
    quality_signal_refs: list[str] = Field(default_factory=list)
    status: ReviewInvocationStatus
    result_summary: str | None = None
    result_evidence_refs: list[str] = Field(default_factory=list)
    target_role: str = "Chair"
    created_at: str
    completed_at: str | None = None
    ai_authored: bool = True
    schema_version: int = 1


AccountType = Literal["goal", "boundary", "decision", "evidence", "debt", "learning"]
AccountDecisionValue = Literal["adopt", "revise", "defer", "reject"]


class AccountUpdateProposalCreateRequest(BaseModel):
    round_id: str | None = None
    task_id: str | None = None
    proposed_by_role: str = Field(..., min_length=1, max_length=64)
    account_type: AccountType
    proposed_change: str = Field(..., min_length=1, max_length=4000)
    rationale: str = Field(..., min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list)
    quality_signal_refs: list[str] = Field(default_factory=list)
    review_invocation_refs: list[str] = Field(default_factory=list)
    target_role: str = Field(default="Chair", min_length=1, max_length=64)


class AccountUpdateProposalResponse(BaseModel):
    proposal_id: str
    thread_id: str
    run_id: str
    round_id: str | None = None
    task_id: str | None = None
    proposed_by_role: str
    account_type: AccountType
    proposed_change: str
    rationale: str
    evidence_refs: list[str] = Field(default_factory=list)
    quality_signal_refs: list[str] = Field(default_factory=list)
    review_invocation_refs: list[str] = Field(default_factory=list)
    target_role: str = "Chair"
    created_at: str
    ai_authored: bool = True
    schema_version: int = 1


class AccountDecisionCreateRequest(BaseModel):
    proposal_id: str = Field(..., min_length=1, max_length=128)
    decided_by_role: str = Field(default="chair", min_length=1, max_length=64)
    decision: AccountDecisionValue
    rationale: str = Field(..., min_length=1, max_length=4000)
    revised_change: str | None = Field(default=None, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list)


class AccountDecisionResponse(BaseModel):
    decision_id: str
    proposal_id: str
    thread_id: str
    run_id: str
    decided_by_role: str = "chair"
    decision: AccountDecisionValue
    rationale: str
    revised_change: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    created_at: str
    ai_authored: bool = True
    schema_version: int = 1


class RoleStateCreateRequest(BaseModel):
    round_id: str | None = None
    role_name: str = Field(..., min_length=1, max_length=64)
    summary: str = Field(..., min_length=1, max_length=4000)
    current_focus: str | None = Field(default=None, max_length=4000)
    open_questions: list[str] = Field(default_factory=list)
    accepted_signals: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    updated_by_role: str = Field(default="chair", min_length=1, max_length=64)
    target_role: str = Field(default="Chair", min_length=1, max_length=64)


class RoleStateResponse(BaseModel):
    state_id: str
    thread_id: str
    role_name: str
    summary: str
    current_focus: str | None = None
    open_questions: list[str] = Field(default_factory=list)
    accepted_signals: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    run_id: str | None = None
    round_id: str | None = None
    updated_by_role: str
    target_role: str = "Chair"
    updated_at: str
    ai_authored: bool = True
    programmatic_decision: bool = False
    auto_dispatch: bool = False
    schema_version: int = 1


class RoundPlanCreateRequest(BaseModel):
    round_id: str | None = None
    goal: str = Field(..., min_length=1, max_length=4000)
    boundary: str | None = Field(default=None, max_length=4000)
    evidence_standard: str | None = Field(default=None, max_length=4000)
    capability_release: list[str] = Field(default_factory=list)
    status: str = Field(default="planned", max_length=64)
    dispatch_plan: str | None = Field(default=None, max_length=4000)
    planned_lanes: list[dict[str, Any]] = Field(default_factory=list)
    created_by_role: str = Field(default="chair", min_length=1, max_length=64)


class RoundPlanResponse(RoundPlanCreateRequest):
    plan_id: str
    thread_id: str
    run_id: str
    created_at: str
    updated_at: str
    ai_authored: bool = True
    programmatic_decision: bool = False
    auto_dispatch: bool = False
    schema_version: int = 1


class PlannedLaneCreateRequest(BaseModel):
    round_id: str | None = None
    target_role: str = Field(..., min_length=1, max_length=64)
    reason: str = Field(..., min_length=1, max_length=4000)
    expected_evidence: str | None = Field(default=None, max_length=4000)
    status: LaneStatus = "planned"
    linked_task_id: str | None = Field(default=None, max_length=128)
    evidence_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)


class PlannedLaneUpdateRequest(BaseModel):
    status: LaneStatus
    linked_task_id: str | None = Field(default=None, max_length=128)
    evidence_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)


class PlannedLaneResponse(PlannedLaneCreateRequest):
    lane_id: str
    thread_id: str
    run_id: str
    created_at: str
    updated_at: str
    dispatched_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    ai_authored: bool = True
    programmatic_decision: bool = False
    auto_dispatch: bool = False
    schema_version: int = 1


class ChairDecisionCreateRequest(BaseModel):
    round_id: str | None = None
    decision_type: str = Field(..., min_length=1, max_length=64)
    status: Literal["recorded", "resolved", "superseded"] = "recorded"
    decision: str = Field(..., min_length=1, max_length=4000)
    reason: str = Field(..., min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list)
    affected_lanes: list[str] = Field(default_factory=list)
    handoffs: list[str] = Field(default_factory=list)
    created_by_role: str = Field(default="chair", min_length=1, max_length=64)


class ChairDecisionResponse(ChairDecisionCreateRequest):
    decision_id: str
    thread_id: str
    run_id: str
    created_at: str
    ai_authored: bool = True
    programmatic_decision: bool = False
    auto_dispatch: bool = False
    schema_version: int = 1


class PendingHandoffResolveRequest(BaseModel):
    status: Literal["accepted", "dismissed", "superseded"]
    resolved_by_role: str = Field(default="chair", min_length=1, max_length=64)
    resolution_note: str | None = Field(default=None, max_length=4000)


class PendingHandoffResponse(BaseModel):
    handoff_id: str
    thread_id: str
    run_id: str
    round_id: str | None = None
    task_id: str | None = None
    source_role: str
    target_role: str
    task_or_question: str
    status: PendingHandoffStatus
    handoff: dict[str, Any] = Field(default_factory=dict)
    evidence_strength: str = "Unverified"
    evidence_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    resolved_by_role: str | None = None
    resolution_note: str | None = None
    ai_authored: bool = True
    programmatic_dispatch: bool = False
    auto_dispatch: bool = False
    schema_version: int = 1


class RunQualityContextResponse(BaseModel):
    thread_id: str
    run_id: str
    round_id: str | None = None
    round_state: dict[str, Any] | None = None
    handoffs: list[dict[str, Any]] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    capability_snapshot: dict[str, Any] = Field(default_factory=dict)
    quality_signals: list[QualitySignalResponse] = Field(default_factory=list)
    quality_signal_summary: dict[str, Any] = Field(default_factory=dict)
    quality_verdict: None = None
    auto_rework: bool = False


class ChairOperatingBriefResponse(BaseModel):
    thread_id: str
    run_id: str
    round_id: str | None = None
    task_id: str | None = None
    generated_at: str
    capability_snapshot_version: int | None = None
    handoff_count: int = 0
    latest_handoff: dict[str, Any] | None = None
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    quality_signals: list[dict[str, Any]] = Field(default_factory=list)
    review_invocations: list[dict[str, Any]] = Field(default_factory=list)
    account_proposals: list[dict[str, Any]] = Field(default_factory=list)
    account_decisions: list[dict[str, Any]] = Field(default_factory=list)
    known_gaps: list[str] = Field(default_factory=list)
    source_counts: dict[str, int] = Field(default_factory=dict)


class ThreadTokenUsageModelBreakdown(BaseModel):
    tokens: int = 0
    runs: int = Field(
        default=0,
        description="Number of runs in which this model appeared; counts are non-exclusive for runs that used multiple models.",
    )


class ThreadTokenUsageCallerBreakdown(BaseModel):
    lead_agent: int = 0
    subagent: int = 0
    middleware: int = 0


class ThreadTokenUsageResponse(BaseModel):
    thread_id: str
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_runs: int = 0
    by_model: dict[str, ThreadTokenUsageModelBreakdown] = Field(default_factory=dict)
    by_caller: ThreadTokenUsageCallerBreakdown = Field(default_factory=ThreadTokenUsageCallerBreakdown)


class ThreadContextUsageSnapshot(BaseModel):
    run_id: str
    caller: str = "lead_agent"
    llm_call_index: int = 0
    message_count: int = 0
    tool_schema_count: int = 0
    char_count: int = 0
    estimated_tokens: int = 0
    role_counts: dict[str, int] = Field(default_factory=dict)
    seq: int = 0
    created_at: str = ""


class ThreadContextUsageResponse(BaseModel):
    thread_id: str
    latest: ThreadContextUsageSnapshot | None = None
    latest_lead: ThreadContextUsageSnapshot | None = None
    by_caller: dict[str, ThreadContextUsageSnapshot] = Field(default_factory=dict)
    recent: list[ThreadContextUsageSnapshot] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _supports_user_id_keyword(callable_obj: Any) -> bool:
    """Return True when a store method can accept ``user_id=...``."""
    try:
        parameters = inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        return False
    return "user_id" in parameters or any(param.kind is inspect.Parameter.VAR_KEYWORD for param in parameters.values())


def _cancel_conflict_detail(run_id: str, record: RunRecord) -> str:
    if is_inflight_status(record.status):
        return f"Run {run_id} is not active on this worker and cannot be cancelled"
    return f"Run {run_id} is not cancellable (status: {run_status_value(record.status)})"


async def _resolve_thread_run_for_user(thread_id: str, run_id: str, request: Request, *, user_id: str) -> RunRecord:
    run_mgr = get_run_manager(request)
    record = await run_mgr.get(run_id, user_id=user_id)
    if record is None or record.thread_id != thread_id:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return record


def _normalize_run_terminal_reason(reason: str | None) -> str | None:
    if not reason:
        return None
    return _RUN_TERMINAL_REASON_ALIASES.get(reason, reason)


def _run_terminal_reason(record: RunRecord) -> str | None:
    stored_reason = _normalize_run_terminal_reason(getattr(record, "terminal_reason", None))
    if stored_reason is not None:
        return stored_reason
    status = run_status_value(record.status)
    if status == RunStatus.success.value:
        return "success"
    if status in {RunStatus.timeout.value, "timed_out"}:
        return "timeout"
    if status in {RunStatus.interrupted.value, "cancelled"}:
        return "cancelled"
    if status in {"worker_lost", "boundary_stopped", "rolled_back", "rollback_failed"}:
        return status
    if status == RunStatus.error.value:
        error = (record.error or "").strip().lower()
        if "rolled back by user" in error:
            return "rolled_back"
        if any(marker in error for marker in _WORKER_LOST_ERROR_MARKERS):
            return "worker_lost"
        return "failed"
    return None


def run_error_for_response(error: str | None) -> str | None:
    if not error:
        return None
    text = error.strip()
    if _is_provider_transient_run_error(text):
        return _PUBLIC_PROVIDER_TRANSIENT_RUN_ERROR
    if "\n" in text:
        return _public_run_error_from_multiline(text) or "Run failed"
    if not _is_public_run_error_text(text):
        return "Run failed"
    return text


def _is_provider_transient_run_error(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _PROVIDER_TRANSIENT_RUN_ERROR_MARKERS)


def _is_public_run_error_text(text: str) -> bool:
    if not text or len(text) > _MAX_PUBLIC_RUN_ERROR_CHARS:
        return False
    lowered = text.lower()
    return not any(marker in lowered for marker in _SENSITIVE_RUN_ERROR_MARKERS)


def _public_run_error_from_multiline(text: str) -> str | None:
    for raw_line in reversed(text.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(_RUN_ERROR_TRACEBACK_FRAME_PREFIXES):
            continue
        match = _RUN_ERROR_EXCEPTION_RE.match(line)
        if not match:
            continue
        message = match.group("message").strip()
        if _is_provider_transient_run_error(message):
            return _PUBLIC_PROVIDER_TRANSIENT_RUN_ERROR
        if _is_public_run_error_text(message):
            return message
        return None
    return None


def _terminal_round_target(record: RunRecord) -> tuple[str, str] | None:
    status = run_status_value(record.status)
    if not is_terminal_status(status):
        return None
    if status == RunStatus.success.value:
        return "closed", "round.closed"
    return "blocked", "round.blocked"


def _terminal_task_lane_status(record: RunRecord) -> str | None:
    status = run_status_value(record.status)
    if not is_terminal_status(status):
        return None
    reason = _run_terminal_reason(record)
    if status == RunStatus.success.value:
        return "completed"
    if status in {RunStatus.timeout.value, "timed_out"} or reason == "timeout":
        return "timed_out"
    if status in {RunStatus.interrupted.value, "cancelled", "rolled_back"} or reason in {"cancelled", "rolled_back"}:
        return "cancelled"
    return "failed"


def _terminal_task_lane_reason(record: RunRecord) -> str | None:
    lane_status = _terminal_task_lane_status(record)
    reason = _run_terminal_reason(record)
    if lane_status == "timed_out":
        return "timed_out"
    if lane_status == "cancelled":
        return "user_cancelled"
    if reason == "boundary_stopped":
        return "boundary_blocked"
    if lane_status == "failed":
        return "failed"
    return None


def _terminal_task_lane_error(record: RunRecord) -> str | None:
    lane_status = _terminal_task_lane_status(record)
    if lane_status == "completed":
        return None
    reason = _run_terminal_reason(record)
    if lane_status == "timed_out":
        return "Parent run timed out before this task lane completed."
    if lane_status == "cancelled":
        return "Parent run stopped before this task lane completed."
    if reason:
        return f"Parent run ended before this task lane completed: {reason}."
    status = run_status_value(record.status) or "terminal"
    return f"Parent run ended before this task lane completed with status {status}."


async def _list_runtime_snapshot_task_lane_rows(
    round_store: Any,
    *,
    thread_id: str,
    round_rows: list[dict[str, Any]],
    user_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    if not hasattr(round_store, "list_task_lanes_by_round"):
        return []
    task_lane_rows: list[dict[str, Any]] = []
    remaining = limit
    for round_row in round_rows:
        if remaining <= 0:
            break
        round_id = round_row.get("round_id")
        if not isinstance(round_id, str) or not round_id:
            continue
        rows = await round_store.list_task_lanes_by_round(
            thread_id=thread_id,
            round_id=round_id,
            user_id=user_id,
            limit=remaining,
        )
        task_lane_rows.extend(rows or [])
        remaining -= len(rows or [])
    return task_lane_rows


def _task_projection_event_content(event: dict[str, Any]) -> dict[str, Any]:
    content = event.get("content")
    if isinstance(content, dict):
        payload = dict(content)
    else:
        payload = {}
    payload.setdefault("thread_id", event.get("thread_id"))
    payload.setdefault("run_id", event.get("run_id"))
    payload.setdefault("type", event.get("event_type"))
    payload.setdefault("event_type", event.get("event_type"))
    metadata = event.get("metadata")
    if isinstance(metadata, dict):
        payload.setdefault("task_id", metadata.get("task_id"))
    payload["projection_repair"] = True
    payload["source"] = "runtime_snapshot_event_projection_repair"
    payload["observed_by"] = "system-observed"
    payload["source_event_seq"] = event.get("seq")
    return payload


async def _repair_task_event_projection_from_store(
    *,
    event_store: Any,
    round_store: Any,
    records: list[RunRecord],
    round_rows: list[dict[str, Any]],
    task_lane_rows: list[dict[str, Any]],
    user_id: str | None,
) -> bool:
    if not hasattr(event_store, "list_events") or not hasattr(round_store, "record_task_events"):
        return False
    round_run_ids = {row.get("current_run_id") for row in round_rows if isinstance(row.get("current_run_id"), str)}
    if not round_run_ids:
        return False
    lanes_by_task = {(lane.get("run_id"), lane.get("task_id")): lane for lane in task_lane_rows}
    repair_events: list[dict[str, Any]] = []
    for record in records:
        if record.run_id not in round_run_ids:
            continue
        kwargs: dict[str, Any] = {"event_types": _TASK_EVENT_PROJECTION_TYPES, "limit": 500}
        if _supports_user_id_keyword(event_store.list_events):
            kwargs["user_id"] = user_id
        try:
            events = await event_store.list_events(record.thread_id, record.run_id, **kwargs)
        except Exception:
            logger.warning("Failed to read task events for runtime snapshot projection repair", exc_info=True)
            continue
        latest_by_task: dict[str, dict[str, Any]] = {}
        for event in events or []:
            payload = _task_projection_event_content(event)
            task_id = payload.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                continue
            latest_by_task[task_id] = payload
        for task_id, payload in latest_by_task.items():
            event_type = str(payload.get("type") or payload.get("event_type") or "")
            terminal_status = _TASK_EVENT_TERMINAL_STATUS_BY_TYPE.get(event_type)
            if terminal_status is not None:
                payload["status"] = terminal_status
            lane = lanes_by_task.get((record.run_id, task_id))
            needs_repair = lane is None or not lane.get("status") or (terminal_status is not None and lane.get("status") in _ACTIVE_TASK_LANE_STATUSES)
            if needs_repair:
                repair_events.append(payload)
    if not repair_events:
        return False
    try:
        await round_store.record_task_events(repair_events)
    except Exception:
        logger.warning("Failed to repair task event projection from runtime snapshot", exc_info=True)
        return False
    return True


async def _repair_terminal_runtime_snapshot_rows(
    *,
    round_store: Any,
    records: list[RunRecord],
    round_rows: list[dict[str, Any]],
    task_lane_rows: list[dict[str, Any]],
) -> RuntimeSnapshotSelfHealResponse:
    recovery = RuntimeSnapshotSelfHealResponse()
    terminal_records = {record.run_id: record for record in records if is_terminal_status(record.status)}
    if not terminal_records:
        return recovery

    if hasattr(round_store, "set_run_state"):
        for row in round_rows:
            run_id = row.get("current_run_id")
            if not isinstance(run_id, str):
                continue
            record = terminal_records.get(run_id)
            if record is None:
                continue
            target = _terminal_round_target(record)
            if target is None:
                continue
            state, event_type = target
            if row.get("state") in _TERMINAL_ROUND_STATES:
                continue
            try:
                updated = await round_store.set_run_state(
                    run_id,
                    state=state,
                    event_type=event_type,
                    content={
                        "source": "runtime_snapshot_recovery",
                        "run_status": run_status_value(record.status),
                        "terminal_reason": _run_terminal_reason(record),
                        "error": run_error_for_response(record.error),
                    },
                )
            except ValueError:
                logger.debug("Skipped terminal round snapshot repair for run %s", sanitize_log_param(run_id), exc_info=True)
                continue
            except Exception:
                logger.warning("Failed to repair terminal round snapshot state for run %s", sanitize_log_param(run_id), exc_info=True)
                continue
            if updated is None:
                continue
            round_id = updated.get("round_id") if isinstance(updated, dict) else row.get("round_id")
            updated_state = updated.get("state") if isinstance(updated, dict) else state
            if isinstance(round_id, str) and isinstance(updated_state, str):
                recovery.rounds.append(
                    RuntimeSnapshotSelfHealRoundResponse(
                        run_id=run_id,
                        round_id=round_id,
                        state=updated_state,
                    )
                )

    if not hasattr(round_store, "record_task_events"):
        recovery.round_count = len(recovery.rounds)
        recovery.task_lane_count = len(recovery.task_lanes)
        recovery.repaired = recovery.round_count > 0
        return recovery

    task_events: list[dict[str, Any]] = []
    task_lane_recoveries: list[RuntimeSnapshotSelfHealTaskLaneResponse] = []
    for lane in task_lane_rows:
        lane_status = lane.get("status")
        if lane_status not in _ACTIVE_TASK_LANE_STATUSES:
            continue
        run_id = lane.get("run_id")
        task_id = lane.get("task_id")
        if not isinstance(run_id, str) or not isinstance(task_id, str):
            continue
        record = terminal_records.get(run_id)
        if record is None:
            continue
        terminal_status = _terminal_task_lane_status(record)
        if terminal_status is None:
            continue
        round_id = lane.get("round_id")
        if isinstance(round_id, str):
            task_lane_recoveries.append(
                RuntimeSnapshotSelfHealTaskLaneResponse(
                    run_id=run_id,
                    round_id=round_id,
                    task_id=task_id,
                    status=terminal_status,
                )
            )
        event_type = _TASK_LANE_TERMINAL_EVENT_BY_STATUS[terminal_status]
        error_preview = _terminal_task_lane_error(record)
        task_events.append(
            {
                "schema_version": "deerflow.task-event/v1",
                "type": event_type,
                "event_type": event_type,
                "thread_id": lane.get("thread_id"),
                "run_id": run_id,
                "task_id": task_id,
                "status": terminal_status,
                "started_at": lane.get("created_at"),
                "completed_at": lane.get("updated_at"),
                "duration_ms": None,
                "result_preview": None,
                "error_preview": error_preview,
                "artifact_refs": [],
                "action_result": {
                    "status": terminal_status,
                    "terminal_reason": _terminal_task_lane_reason(record),
                    "error": error_preview,
                },
                "usage": {},
            }
        )
    if not task_events:
        recovery.round_count = len(recovery.rounds)
        recovery.task_lane_count = len(recovery.task_lanes)
        recovery.repaired = recovery.round_count > 0
        return recovery
    try:
        await round_store.record_task_events(task_events)
    except Exception:
        logger.warning("Failed to repair terminal task lane snapshot state", exc_info=True)
    else:
        recovery.task_lanes.extend(task_lane_recoveries)
    recovery.round_count = len(recovery.rounds)
    recovery.task_lane_count = len(recovery.task_lanes)
    recovery.repaired = recovery.round_count > 0 or recovery.task_lane_count > 0
    return recovery


def _artifact_file_metadata(thread_id: str, virtual_path: str, *, user_id: str | None) -> dict[str, Any]:
    if ".skill/" in virtual_path:
        return {}
    actual_path = resolve_thread_virtual_path(thread_id, virtual_path, user_id=user_id)
    if not actual_path.is_file():
        return {"available": False}

    mime_type, _ = mimetypes.guess_type(actual_path)
    digest = hashlib.sha256()
    size = 0
    sample = b""
    with actual_path.open("rb") as file:
        while chunk := file.read(_ARTIFACT_HASH_CHUNK_BYTES):
            size += len(chunk)
            if len(sample) < _ARTIFACT_TEXT_SAMPLE_BYTES:
                sample += chunk[: _ARTIFACT_TEXT_SAMPLE_BYTES - len(sample)]
            digest.update(chunk)

    display_policy = "inline"
    if mime_type in _ACTIVE_ARTIFACT_MIME_TYPES or (mime_type is None and b"\x00" in sample):
        display_policy = "attachment"

    metadata: dict[str, Any] = {
        "available": True,
        "display_policy": display_policy,
        "sha256": digest.hexdigest(),
        "size_bytes": size,
    }
    if mime_type:
        metadata["mime_type"] = mime_type
    return metadata


def _attach_artifact_file_metadata(entries: list[dict[str, Any]], thread_id: str, *, user_id: str | None) -> list[dict[str, Any]]:
    for entry in entries:
        virtual_path = entry.get("virtual_path")
        if not isinstance(virtual_path, str) or not virtual_path:
            continue
        try:
            entry.update(_artifact_file_metadata(thread_id, virtual_path, user_id=user_id))
        except Exception:
            logger.debug("Could not fingerprint artifact %s for thread %s", virtual_path, sanitize_log_param(thread_id), exc_info=True)
    return entries


async def _persist_artifact_index(request: Request, entries: list[dict[str, Any]], *, user_id: str | None) -> None:
    if not entries:
        return
    repo = getattr(request.app.state, "artifact_provenance_repo", None)
    if repo is None:
        return
    try:
        await repo.upsert_many(entries, user_id=user_id)
    except Exception:
        logger.warning("Failed to persist artifact provenance index", exc_info=True)


def _event_content(event: dict[str, Any]) -> dict[str, Any]:
    content = event.get("content")
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _event_metadata(event: dict[str, Any]) -> dict[str, Any]:
    metadata = event.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _event_round_id(event: dict[str, Any], fallback: str | None) -> str | None:
    content = _event_content(event)
    value = content.get("round_id") or _event_metadata(event).get("round_id") or fallback
    return value if isinstance(value, str) and value else None


def _event_task_id(event: dict[str, Any]) -> str | None:
    content = _event_content(event)
    value = content.get("task_id") or _event_metadata(event).get("task_id")
    return value if isinstance(value, str) and value else None


def _event_produced_by(event: dict[str, Any]) -> str:
    metadata = _event_metadata(event)
    for key in ("source_tool", "caller", "source_node"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return value
    event_type = event.get("event_type")
    return event_type if isinstance(event_type, str) and event_type else "runtime"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _message_content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(parts)
    return ""


def _string_refs(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        refs: list[str] = []
        for key in ("ref", "virtual_path", "path", "source_ref", "output_ref"):
            refs.extend(_string_refs(value.get(key)))
        return refs
    if isinstance(value, list):
        refs: list[str] = []
        for item in value:
            refs.extend(_string_refs(item))
        return refs
    return []


def _append_evidence_ref(
    refs: list[dict[str, Any]],
    seen: set[tuple[str, str, str | None]],
    raw_ref: str,
    *,
    thread_id: str,
    run_id: str,
    round_id: str | None,
    task_id: str | None,
    claim: str = "",
    produced_by: str,
    created_at: str | None,
    source_kind: str | None = None,
    excerpt: str | None = None,
    content_sha256: str | None = None,
) -> None:
    normalized = normalize_evidence_ref(
        raw_ref,
        thread_id=thread_id,
        run_id=run_id,
        round_id=round_id,
        task_id=task_id,
        claim=claim,
        produced_by=produced_by,
        created_at=created_at,
        source_kind=source_kind,
        excerpt=excerpt,
        sha256=content_sha256,
    )
    key = (normalized["ref"], normalized["source_kind"], normalized["task_id"])
    if key in seen:
        return
    seen.add(key)
    refs.append(normalized)


def _append_action_result_evidence(
    refs: list[dict[str, Any]],
    seen: set[tuple[str, str, str | None]],
    event: dict[str, Any],
    *,
    thread_id: str,
    run_id: str,
    round_id: str | None,
) -> None:
    content = _event_content(event)
    action_result = content.get("action_result")
    if not isinstance(action_result, dict):
        metadata_action_result = _event_metadata(event).get("action_result")
        action_result = metadata_action_result if isinstance(metadata_action_result, dict) else None
    if not isinstance(action_result, dict):
        return

    task_id = _event_task_id(event) or (action_result.get("action_id") if isinstance(action_result.get("action_id"), str) else None)
    claim = _message_content_text(action_result.get("summary") or action_result.get("description") or "")
    produced_by = _event_produced_by(event)
    created_at = str(event.get("created_at") or "")
    for evidence_ref in _string_refs(action_result.get("evidence_refs")):
        _append_evidence_ref(
            refs,
            seen,
            evidence_ref,
            thread_id=thread_id,
            run_id=run_id,
            round_id=round_id,
            task_id=task_id,
            claim=claim,
            produced_by=produced_by,
            created_at=created_at,
        )
    output_ref = action_result.get("output_ref")
    if isinstance(output_ref, str) and output_ref:
        _append_evidence_ref(
            refs,
            seen,
            f"output_ref: {output_ref}",
            thread_id=thread_id,
            run_id=run_id,
            round_id=round_id,
            task_id=task_id,
            claim=claim,
            produced_by=produced_by,
            created_at=created_at,
            source_kind="output_ref",
        )


def _append_tool_result_evidence(
    refs: list[dict[str, Any]],
    seen: set[tuple[str, str, str | None]],
    event: dict[str, Any],
    *,
    thread_id: str,
    run_id: str,
    round_id: str | None,
) -> None:
    if event.get("event_type") != "llm.tool.result":
        return
    content = _event_content(event)
    tool_name = content.get("name")
    text = _message_content_text(content)
    if not text:
        return
    lower = text.lower()
    looks_like_command_output = tool_name in {"bash", "bash_tool"} or any(marker in lower for marker in ("stdout", "stderr", "std error", "exit code", "exit_code", "returncode"))
    if not looks_like_command_output:
        return
    _append_evidence_ref(
        refs,
        seen,
        f"command output: {text}",
        thread_id=thread_id,
        run_id=run_id,
        round_id=round_id,
        task_id=_event_task_id(event),
        claim="tool result output summary",
        produced_by=str(tool_name or _event_produced_by(event)),
        created_at=str(event.get("created_at") or ""),
        source_kind="command_output",
        excerpt=text,
        content_sha256=_sha256_text(text),
    )


def _append_log_evidence(
    refs: list[dict[str, Any]],
    seen: set[tuple[str, str, str | None]],
    event: dict[str, Any],
    *,
    thread_id: str,
    run_id: str,
    round_id: str | None,
) -> None:
    if event.get("event_type") not in {"run.error", "llm.error"}:
        return
    text = _message_content_text(event.get("content"))
    if not text:
        text = str(event.get("content") or "")
    if not text:
        return
    _append_evidence_ref(
        refs,
        seen,
        f"log: {text}",
        thread_id=thread_id,
        run_id=run_id,
        round_id=round_id,
        task_id=_event_task_id(event),
        claim="runtime error log summary",
        produced_by=_event_produced_by(event),
        created_at=str(event.get("created_at") or ""),
        source_kind="log",
        excerpt=text,
        content_sha256=_sha256_text(text),
    )


def _run_evidence_summary(refs: list[dict[str, Any]]) -> dict[str, Any]:
    by_source_kind: dict[str, int] = {}
    by_strength = {"Strong": 0, "Weak": 0, "Unverified": 0}
    for ref in refs:
        source_kind = str(ref.get("source_kind") or "unknown")
        strength = str(ref.get("strength") or "Unverified")
        by_source_kind[source_kind] = by_source_kind.get(source_kind, 0) + 1
        if strength in by_strength:
            by_strength[strength] += 1
    return {
        "total": len(refs),
        "by_source_kind": by_source_kind,
        "by_strength": by_strength,
        "quality_verdict": None,
        "auto_rework": False,
    }


def _run_evidence_refs_from_events(events: list[dict[str, Any]], *, thread_id: str, run_id: str, round_id: str | None) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None]] = set()
    for event in events:
        event_round_id = _event_round_id(event, round_id)
        _append_action_result_evidence(refs, seen, event, thread_id=thread_id, run_id=run_id, round_id=event_round_id)
        _append_tool_result_evidence(refs, seen, event, thread_id=thread_id, run_id=run_id, round_id=event_round_id)
        _append_log_evidence(refs, seen, event, thread_id=thread_id, run_id=run_id, round_id=event_round_id)

    for artifact in build_artifact_index(events):
        virtual_path = artifact.get("virtual_path")
        if not isinstance(virtual_path, str) or not virtual_path:
            continue
        provenance = artifact.get("provenance") if isinstance(artifact.get("provenance"), dict) else {}
        _append_evidence_ref(
            refs,
            seen,
            virtual_path,
            thread_id=thread_id,
            run_id=run_id,
            round_id=round_id,
            task_id=artifact.get("task_id") if isinstance(artifact.get("task_id"), str) else None,
            claim="runtime artifact reference",
            produced_by=str(artifact.get("source_tool") or provenance.get("caller") or "runtime"),
            created_at=str(artifact.get("created_at") or ""),
            source_kind="artifact",
            content_sha256=artifact.get("sha256") if isinstance(artifact.get("sha256"), str) else None,
        )
    return refs


async def _run_evidence_payload(thread_id: str, run_id: str, record: RunRecord, request: Request, *, user_id: str, limit: int) -> dict[str, Any]:
    event_store = get_run_event_store(request)
    list_events_kwargs: dict[str, Any] = {"event_types": _RUN_EVIDENCE_EVENT_TYPES, "limit": limit}
    if _supports_user_id_keyword(event_store.list_events):
        list_events_kwargs["user_id"] = user_id
    events = await event_store.list_events(thread_id, run_id, **list_events_kwargs)
    evidence_refs = _run_evidence_refs_from_events(events, thread_id=thread_id, run_id=run_id, round_id=record.round_id)
    return {
        "thread_id": thread_id,
        "run_id": run_id,
        "round_id": record.round_id,
        "evidence_refs": evidence_refs,
        "summary": _run_evidence_summary(evidence_refs),
    }


def _quality_signal_summary(signals: list[dict[str, Any]]) -> dict[str, Any]:
    by_recommendation: dict[str, int] = {}
    for signal in signals:
        recommendation = str(signal.get("recommendation") or "unknown")
        by_recommendation[recommendation] = by_recommendation.get(recommendation, 0) + 1
    return {
        "total": len(signals),
        "by_recommendation": by_recommendation,
        "ai_authored": True,
        "quality_verdict": None,
        "auto_rework": False,
    }


def _review_invocation_by_id(invocations: list[dict[str, Any]], invocation_id: str) -> dict[str, Any] | None:
    for invocation in invocations:
        if invocation.get("invocation_id") == invocation_id:
            return invocation
    return None


def _account_proposal_by_id(proposals: list[dict[str, Any]], proposal_id: str) -> dict[str, Any] | None:
    for proposal in proposals:
        if proposal.get("proposal_id") == proposal_id:
            return proposal
    return None


async def _round_quality_context(thread_id: str, record: RunRecord, request: Request, *, user_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    round_store = get_round_state_store(request)
    round_state = None
    round_context = record.metadata.get("round_context") if isinstance(record.metadata, dict) else None
    task_lanes: list[dict[str, Any]] = []
    if round_store is None:
        round_state = dict(round_context) if isinstance(round_context, dict) else None
        return round_state, task_lanes
    if hasattr(round_store, "list_by_thread"):
        rows = await round_store.list_by_thread(thread_id, user_id=user_id, limit=50)
        for row in rows:
            if row.get("round_id") == record.round_id or row.get("current_run_id") == record.run_id:
                round_state = row
                break
    if round_state is None and isinstance(round_context, dict):
        round_state = dict(round_context)
    if record.round_id and hasattr(round_store, "list_task_lanes_by_round"):
        task_lanes = await round_store.list_task_lanes_by_round(thread_id=thread_id, round_id=record.round_id, user_id=user_id, limit=100)
    return round_state, task_lanes


def _handoffs_from_task_lanes(task_lanes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    handoffs: list[dict[str, Any]] = []
    for lane in task_lanes:
        handoff = lane.get("handoff")
        if not isinstance(handoff, dict):
            continue
        handoffs.append(
            {
                "task_id": lane.get("task_id"),
                "run_id": lane.get("run_id"),
                "round_id": lane.get("round_id"),
                "role": lane.get("role"),
                "status": lane.get("status"),
                "handoff": handoff,
            }
        )
    return handoffs


def _record_to_response(record: RunRecord, round_state: dict[str, Any] | None = None) -> RunResponse:
    round_context = round_state or (record.metadata.get("round_context") if isinstance(record.metadata, dict) else None)
    round_context = round_context if isinstance(round_context, dict) else {}
    return RunResponse(
        run_id=record.run_id,
        thread_id=record.thread_id,
        round_id=record.round_id,
        round_state=round_context.get("state") if isinstance(round_context.get("state"), str) else None,
        assistant_id=record.assistant_id,
        status=run_status_value(record.status) or RunStatus.error.value,
        terminal_reason=_run_terminal_reason(record),
        metadata=record.metadata,
        kwargs=record.kwargs,
        multitask_strategy=record.multitask_strategy,
        created_at=record.created_at,
        updated_at=record.updated_at,
        total_input_tokens=record.total_input_tokens,
        total_output_tokens=record.total_output_tokens,
        total_tokens=record.total_tokens,
        llm_call_count=record.llm_call_count,
        lead_agent_tokens=record.lead_agent_tokens,
        subagent_tokens=record.subagent_tokens,
        middleware_tokens=record.middleware_tokens,
        message_count=record.message_count,
    )


async def _sync_thread_error_for_latest_worker_lost(thread_id: str, request: Request, *, records: list[RunRecord], user_id: str | None) -> None:
    if not records:
        return
    latest = records[0]
    if run_status_value(latest.status) != RunStatus.error.value or _run_terminal_reason(latest) != "worker_lost":
        return
    try:
        await get_thread_store(request).update_status(thread_id, "error", user_id=user_id)
    except Exception:
        logger.debug("Failed to mark thread %s error after stale run recovery", sanitize_log_param(thread_id), exc_info=True)


def _message_id(message: Any) -> str | None:
    value = getattr(message, "id", None)
    if value is None and isinstance(message, dict):
        value = message.get("id")
    return str(value) if value else None


def _message_type(message: Any) -> str | None:
    value = getattr(message, "type", None)
    if value is None and isinstance(message, dict):
        value = message.get("type") or message.get("role")
    if value == "assistant":
        return "ai"
    return str(value) if value else None


def _message_name(message: Any) -> str | None:
    value = getattr(message, "name", None)
    if value is None and isinstance(message, dict):
        value = message.get("name")
    return str(value) if value else None


def _message_content(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", None)


def _message_text(message: Any) -> str:
    return message_to_text(message)


def _message_additional_kwargs(message: Any) -> dict[str, Any]:
    value = getattr(message, "additional_kwargs", None)
    if value is None and isinstance(message, dict):
        value = message.get("additional_kwargs")
    return dict(value or {}) if isinstance(value, dict) else {}


def _is_hidden_or_control_message(message: Any) -> bool:
    message_type = _message_type(message)
    additional_kwargs = _message_additional_kwargs(message)
    name = _message_name(message)
    return message_type == "remove" or (name is not None and name in HIDDEN_CONTROL_MESSAGE_NAMES) or additional_kwargs.get("hide_from_ui") is True


def _is_visible_human_message(message: Any) -> bool:
    return _message_type(message) == "human" and not _is_hidden_or_control_message(message)


def _is_visible_ai_message(message: Any) -> bool:
    return _message_type(message) == "ai" and not _is_hidden_or_control_message(message)


def _is_slash_skill_activation_only(message: Any) -> bool:
    if _message_type(message) != "human":
        return False
    text = _message_text(message)
    if "<slash_skill_activation>" not in text:
        return False
    public_text = INTERNAL_CONTEXT_TAG_RE.sub("", text)
    public_text = re.sub(r"^--- BEGIN USER INPUT ---\n?", "", public_text)
    public_text = re.sub(r"\n?--- END USER INPUT ---$", "", public_text).strip()
    return public_text == ""


def _message_display(content: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    caller = str(metadata.get("caller") or "")
    if caller == "task_event":
        return {"visible_in_chat": False, "surface": "control", "reason": "task_event"}
    if caller.startswith("middleware:"):
        return {"visible_in_chat": False, "surface": "control", "reason": "middleware_message"}
    if caller.startswith("subagent:"):
        return {"visible_in_chat": False, "surface": "control", "reason": "subagent_message"}
    additional_kwargs = _message_additional_kwargs(content)
    if additional_kwargs.get("hide_from_ui") is True:
        return {"visible_in_chat": False, "surface": "hidden", "reason": "hide_from_ui"}
    if _message_type(content) == "remove":
        return {"visible_in_chat": False, "surface": "control", "reason": "control_message"}
    name = _message_name(content)
    if name is not None and name in HIDDEN_CONTROL_MESSAGE_NAMES:
        return {"visible_in_chat": False, "surface": "control", "reason": "control_message"}
    if _is_slash_skill_activation_only(content):
        return {"visible_in_chat": False, "surface": "control", "reason": "control_message"}

    message_type = _message_type(content)
    caller = str(metadata.get("caller") or "")
    if message_type == "human":
        reason = "human_message"
    elif message_type == "ai":
        reason = "lead_ai_response"
    elif message_type == "tool":
        return {"visible_in_chat": False, "surface": "control", "reason": "tool_message"}
    else:
        return {"visible_in_chat": False, "surface": "control", "reason": "control_message"}
    return {"visible_in_chat": True, "surface": "chat", "reason": reason}


def attach_message_display(rows: list[dict]) -> list[dict]:
    for row in rows:
        metadata = row.get("metadata")
        row["display"] = _message_display(
            row.get("content"),
            metadata if isinstance(metadata, dict) else {},
        )
    return rows


def _context_usage_snapshot_from_event(event: dict[str, Any]) -> ThreadContextUsageSnapshot | None:
    content = event.get("content")
    if not isinstance(content, dict):
        return None

    role_counts = content.get("role_counts")
    if not isinstance(role_counts, dict):
        role_counts = {}

    def _int_value(key: str) -> int:
        value = content.get(key)
        return value if isinstance(value, int) else 0

    return ThreadContextUsageSnapshot(
        run_id=str(event.get("run_id") or content.get("run_id") or ""),
        caller=str(content.get("caller") or event.get("metadata", {}).get("caller") or "lead_agent"),
        llm_call_index=_int_value("llm_call_index"),
        message_count=_int_value("message_count"),
        tool_schema_count=_int_value("tool_schema_count"),
        char_count=_int_value("char_count"),
        estimated_tokens=_int_value("estimated_tokens"),
        role_counts={str(k): int(v) for k, v in role_counts.items() if isinstance(v, int)},
        seq=event.get("seq") if isinstance(event.get("seq"), int) else 0,
        created_at=str(event.get("created_at") or ""),
    )


def _checkpoint_messages(checkpoint_tuple: Any) -> list[Any]:
    checkpoint = getattr(checkpoint_tuple, "checkpoint", None) or {}
    channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
    messages = channel_values.get("messages", []) if isinstance(channel_values, dict) else []
    return messages if isinstance(messages, list) else []


def _checkpoint_configurable(checkpoint_tuple: Any) -> dict[str, Any]:
    config = getattr(checkpoint_tuple, "config", None) or {}
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    return dict(configurable) if isinstance(configurable, dict) else {}


def _checkpoint_response(checkpoint_tuple: Any) -> dict[str, Any]:
    configurable = _checkpoint_configurable(checkpoint_tuple)
    checkpoint_id = configurable.get("checkpoint_id")
    if not checkpoint_id:
        raise HTTPException(status_code=409, detail="Checkpoint is missing checkpoint_id")
    return {
        "checkpoint_ns": str(configurable.get("checkpoint_ns") or ""),
        "checkpoint_id": str(checkpoint_id),
        "checkpoint_map": configurable.get("checkpoint_map"),
    }


def _clean_human_message_for_regenerate(message: Any) -> dict[str, Any]:
    additional_kwargs = _message_additional_kwargs(message)
    content = get_original_user_content_text(_message_content(message), additional_kwargs)
    additional_kwargs.pop(ORIGINAL_USER_CONTENT_KEY, None)
    additional_kwargs.pop("hide_from_ui", None)

    clean_message: dict[str, Any] = {
        "type": "human",
        "content": [{"type": "text", "text": content}],
        "additional_kwargs": additional_kwargs,
    }
    message_id = _message_id(message)
    if message_id:
        clean_message["id"] = message_id
    name = _message_name(message)
    if name:
        clean_message["name"] = name
    return clean_message


def _event_message_id(row: dict[str, Any]) -> str | None:
    content = row.get("content")
    if isinstance(content, BaseMessage):
        return _message_id(content)
    if isinstance(content, dict):
        return _message_id(content)
    return None


def _run_last_ai_matches_message(record: RunRecord, message: Any) -> bool:
    last_ai_message = (record.last_ai_message or "").strip()
    if not last_ai_message:
        return False
    target_text = _message_text(message).strip()
    if not target_text:
        return False
    return last_ai_message == target_text[: len(last_ai_message)]


async def _find_target_run_id(thread_id: str, message_id: str, target_message: Any, request: Request) -> str:
    event_store = get_run_event_store(request)
    user_id = get_request_storage_user_id(request)
    list_messages_kwargs: dict[str, Any] = {"limit": REGENERATE_HISTORY_SCAN_LIMIT}
    if _supports_user_id_keyword(event_store.list_messages):
        list_messages_kwargs["user_id"] = user_id
    rows = await event_store.list_messages(thread_id, **list_messages_kwargs)
    for row in reversed(rows):
        if row.get("event_type") not in {"ai_message", "llm.ai.response"}:
            continue
        if _event_message_id(row) == message_id:
            run_id = row.get("run_id")
            if isinstance(run_id, str) and run_id:
                return run_id
    run_mgr = get_run_manager(request)
    records = await run_mgr.list_by_thread(thread_id, user_id=user_id, limit=10)
    fallback_record = next(
        (record for record in records if record.status == RunStatus.success and _run_last_ai_matches_message(record, target_message)),
        None,
    )
    if fallback_record is not None:
        return fallback_record.run_id
    if len(rows) >= REGENERATE_HISTORY_SCAN_LIMIT:
        logger.warning(
            "Could not find source run for regenerate message %s in recent run events for thread %s (limit=%s)",
            message_id,
            thread_id,
            REGENERATE_HISTORY_SCAN_LIMIT,
        )
    raise HTTPException(status_code=409, detail="Could not find source run for assistant message")


async def _find_base_checkpoint_before_human(thread_id: str, human_message_id: str, request: Request) -> Any:
    checkpointer = get_checkpointer(request)
    base_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    try:
        checkpoints = [item async for item in checkpointer.alist(base_config, limit=REGENERATE_HISTORY_SCAN_LIMIT)]
    except Exception as exc:
        logger.exception("Failed to list checkpoints for regenerate thread %s", thread_id)
        raise HTTPException(status_code=500, detail="Failed to inspect checkpoint history") from exc

    previous_checkpoint = None
    for checkpoint_tuple in reversed(checkpoints):
        messages = _checkpoint_messages(checkpoint_tuple)
        message_ids = {_message_id(message) for message in messages}
        if human_message_id in message_ids:
            if previous_checkpoint is None:
                raise HTTPException(
                    status_code=409,
                    detail="Could not find an addressable checkpoint before the target user message",
                )
            return previous_checkpoint
        if _checkpoint_configurable(checkpoint_tuple).get("checkpoint_id"):
            previous_checkpoint = checkpoint_tuple

    if len(checkpoints) >= REGENERATE_HISTORY_SCAN_LIMIT:
        logger.warning(
            "Could not locate target user message %s in recent checkpoint history for thread %s (limit=%s)",
            human_message_id,
            thread_id,
            REGENERATE_HISTORY_SCAN_LIMIT,
        )
    raise HTTPException(
        status_code=409,
        detail=(f"Could not locate target user message in recent checkpoint history (limit={REGENERATE_HISTORY_SCAN_LIMIT})"),
    )


async def _prepare_regenerate_payload(thread_id: str, message_id: str, request: Request) -> RegeneratePrepareResponse:
    checkpointer = get_checkpointer(request)
    latest_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    try:
        latest_checkpoint = await checkpointer.aget_tuple(latest_config)
    except Exception as exc:
        logger.exception("Failed to read latest checkpoint for regenerate thread %s", thread_id)
        raise HTTPException(status_code=500, detail="Failed to read latest checkpoint") from exc
    if latest_checkpoint is None:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} has no checkpoint")

    messages = _checkpoint_messages(latest_checkpoint)
    target_index = next((i for i, message in enumerate(messages) if _message_id(message) == message_id), None)
    if target_index is None:
        raise HTTPException(status_code=404, detail=f"Message {message_id} not found")
    target_message = messages[target_index]
    if not _is_visible_ai_message(target_message):
        raise HTTPException(status_code=409, detail="Only visible assistant messages can be regenerated")

    latest_visible_ai = next((message for message in reversed(messages) if _is_visible_ai_message(message)), None)
    if _message_id(latest_visible_ai) != message_id:
        raise HTTPException(status_code=409, detail="Only the latest assistant message can be regenerated")

    previous_human = next((message for message in reversed(messages[:target_index]) if _is_visible_human_message(message)), None)
    if previous_human is None:
        raise HTTPException(status_code=409, detail="Could not find the user message for this assistant response")
    previous_human_id = _message_id(previous_human)
    if not previous_human_id:
        raise HTTPException(status_code=409, detail="The source user message is missing an id")

    base_checkpoint_tuple = await _find_base_checkpoint_before_human(thread_id, previous_human_id, request)
    target_run_id = await _find_target_run_id(thread_id, message_id, target_message, request)
    checkpoint = _checkpoint_response(base_checkpoint_tuple)
    metadata = {
        "regenerate_from_message_id": message_id,
        "regenerate_from_run_id": target_run_id,
        "regenerate_checkpoint_id": checkpoint["checkpoint_id"],
    }
    return RegeneratePrepareResponse(
        input={"messages": [_clean_human_message_for_regenerate(previous_human)]},
        checkpoint=checkpoint,
        metadata=metadata,
        target_run_id=target_run_id,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/{thread_id}/runs/regenerate/prepare", response_model=RegeneratePrepareResponse)
@require_permission("runs", "create", owner_check=True, require_existing=True)
async def prepare_regenerate_run(
    thread_id: str,
    body: RegeneratePrepareRequest,
    request: Request,
) -> RegeneratePrepareResponse:
    """Prepare input and checkpoint for regenerating the latest assistant turn."""
    return await _prepare_regenerate_payload(thread_id, body.message_id, request)


@router.post("/{thread_id}/runs", response_model=RunResponse)
@require_permission("runs", "create", owner_check=True, require_existing=True)
async def create_run(thread_id: str, body: RunCreateRequest, request: Request) -> RunResponse:
    """Create a background run (returns immediately)."""
    record = await start_run(body, thread_id, request)
    return _record_to_response(record)


@router.post("/{thread_id}/runs/stream")
@require_permission("runs", "create", owner_check=True, require_existing=True)
async def stream_run(thread_id: str, body: RunCreateRequest, request: Request) -> StreamingResponse:
    """Create a run and stream events via SSE.

    The response includes a ``Content-Location`` header with the run's
    resource URL, matching the LangGraph Platform protocol.  The
    ``useStream`` React hook uses this to extract run metadata.
    """
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    event_store = get_run_event_store(request)
    user_id = get_request_storage_user_id(request)
    record = await start_run(body, thread_id, request)

    return StreamingResponse(
        sse_consumer(bridge, record, request, run_mgr, event_store=event_store, user_id=user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            # LangGraph Platform includes run metadata in this header.
            # The SDK uses a greedy regex to extract the run id from this path,
            # so it must point at the canonical run resource without extra suffixes.
            "Content-Location": f"/api/threads/{thread_id}/runs/{record.run_id}",
        },
    )


@router.post("/{thread_id}/runs/wait", response_model=dict)
@require_permission("runs", "create", owner_check=True, require_existing=True)
async def wait_run(thread_id: str, body: RunCreateRequest, request: Request) -> dict:
    """Create a run and block until it completes, returning the final state."""
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    user_id = get_request_storage_user_id(request)
    record = await start_run(body, thread_id, request)

    completed = True
    if record.task is not None:
        completed = await wait_for_run_completion(bridge, record, request, run_mgr)

    if completed:
        record = await run_mgr.get(record.run_id, user_id=user_id) or record
    if completed and record.status == RunStatus.success:
        checkpointer = get_checkpointer(request)
        config = {"configurable": {"thread_id": thread_id}}
        try:
            checkpoint_tuple = await checkpointer.aget_tuple(config)
            if checkpoint_tuple is not None:
                checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
                channel_values = checkpoint.get("channel_values", {})
                return serialize_channel_values_for_api(channel_values)
        except Exception:
            logger.exception("Failed to fetch final state for run %s", record.run_id)

    return {"status": run_status_value(record.status), "error": run_error_for_response(record.error)}


@router.get("/{thread_id}/runs", response_model=list[RunResponse])
@require_permission("runs", "read", owner_check=True)
async def list_runs(thread_id: str, request: Request) -> list[RunResponse]:
    """List all runs for a thread."""
    run_mgr = get_run_manager(request)
    user_id = get_request_storage_user_id(request)
    records = await run_mgr.list_by_thread(thread_id, user_id=user_id)
    await _sync_thread_error_for_latest_worker_lost(thread_id, request, records=records, user_id=user_id)
    return [_record_to_response(r) for r in records]


@router.get("/{thread_id}/rounds", response_model=list[RoundResponse])
@require_permission("runs", "read", owner_check=True)
async def list_rounds(
    thread_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[RoundResponse]:
    """List native round-state rows for a thread."""
    round_store = get_round_state_store(request)
    if round_store is None or not hasattr(round_store, "list_by_thread"):
        return []
    user_id = get_request_storage_user_id(request)
    rows = await round_store.list_by_thread(thread_id, user_id=user_id, limit=limit)
    return [RoundResponse.model_validate(row) for row in rows]


@router.get("/{thread_id}/rounds/{round_id}/tasks", response_model=list[TaskLaneResponse])
@require_permission("runs", "read", owner_check=True)
async def list_round_tasks(
    thread_id: str,
    round_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[TaskLaneResponse]:
    """List task lanes for a native round."""
    round_store = get_round_state_store(request)
    if round_store is None or not hasattr(round_store, "list_task_lanes_by_round"):
        return []
    user_id = get_request_storage_user_id(request)
    rows = await round_store.list_task_lanes_by_round(thread_id=thread_id, round_id=round_id, user_id=user_id, limit=limit)
    return [TaskLaneResponse.model_validate(row) for row in rows]


@router.get("/{thread_id}/runtime-snapshot", response_model=ThreadRuntimeSnapshotResponse)
@require_permission("runs", "read", owner_check=True)
async def get_thread_runtime_snapshot(
    thread_id: str,
    request: Request,
    run_limit: int = Query(default=100, ge=1, le=200),
    message_limit: int = Query(default=50, ge=1, le=200),
    round_limit: int = Query(default=50, ge=1, le=200),
    task_lane_limit: int = Query(default=100, ge=1, le=500),
) -> ThreadRuntimeSnapshotResponse:
    """Return one recovery snapshot for thread runtime state."""
    user_id = get_request_storage_user_id(request)
    run_mgr = get_run_manager(request)
    stale_recovered_records: list[RunRecord] = []
    if hasattr(run_mgr, "recover_stale_inflight_runs"):
        stale_recovered_records = await run_mgr.recover_stale_inflight_runs(thread_id=thread_id, user_id=user_id)
    records = await run_mgr.list_by_thread(thread_id, user_id=user_id, limit=run_limit)
    await _sync_thread_error_for_latest_worker_lost(thread_id, request, records=records, user_id=user_id)

    run_messages: list[RuntimeSnapshotRunMessages] = []
    for record in records:
        page = await _run_messages_page(
            thread_id,
            record.run_id,
            record,
            request,
            user_id=user_id,
            limit=message_limit,
        )
        run_messages.append(RuntimeSnapshotRunMessages(run_id=record.run_id, **page))

    rounds: list[RoundResponse] = []
    task_lanes: list[TaskLaneResponse] = []
    round_store = get_round_state_store(request)
    snapshot_self_heal = RuntimeSnapshotSelfHealResponse()
    if round_store is not None and hasattr(round_store, "list_by_thread"):
        round_rows = await round_store.list_by_thread(thread_id, user_id=user_id, limit=round_limit)
        task_lane_rows = await _list_runtime_snapshot_task_lane_rows(
            round_store,
            thread_id=thread_id,
            round_rows=round_rows,
            user_id=user_id,
            limit=task_lane_limit,
        )
        event_projection_repaired = await _repair_task_event_projection_from_store(
            event_store=get_run_event_store(request),
            round_store=round_store,
            records=records,
            round_rows=round_rows,
            task_lane_rows=task_lane_rows,
            user_id=user_id,
        )
        if event_projection_repaired:
            task_lane_rows = await _list_runtime_snapshot_task_lane_rows(
                round_store,
                thread_id=thread_id,
                round_rows=round_rows,
                user_id=user_id,
                limit=task_lane_limit,
            )
        snapshot_self_heal = await _repair_terminal_runtime_snapshot_rows(
            round_store=round_store,
            records=records,
            round_rows=round_rows,
            task_lane_rows=task_lane_rows,
        )
        if event_projection_repaired:
            snapshot_self_heal.repaired = True
        if snapshot_self_heal.repaired:
            round_rows = await round_store.list_by_thread(thread_id, user_id=user_id, limit=round_limit)
            task_lane_rows = await _list_runtime_snapshot_task_lane_rows(
                round_store,
                thread_id=thread_id,
                round_rows=round_rows,
                user_id=user_id,
                limit=task_lane_limit,
            )
        rounds = [RoundResponse.model_validate(row) for row in round_rows]
        task_lanes = [TaskLaneResponse.model_validate(row) for row in task_lane_rows]

    recovery: RuntimeSnapshotRecoveryResponse | None = None
    if stale_recovered_records or snapshot_self_heal.repaired:
        stale_recovery = None
        if stale_recovered_records:
            terminal_reasons = [_run_terminal_reason(record) for record in stale_recovered_records]
            terminal_reason = terminal_reasons[0] if len(set(terminal_reasons)) == 1 else None
            stale_recovery = RuntimeSnapshotStaleInflightRecoveryResponse(
                recovered=True,
                recovered_count=len(stale_recovered_records),
                run_ids=[record.run_id for record in stale_recovered_records],
                terminal_reason=terminal_reason,
                runs=[
                    RuntimeSnapshotRecoveredRunResponse(
                        run_id=record.run_id,
                        terminal_reason=_run_terminal_reason(record),
                    )
                    for record in stale_recovered_records
                ],
            )
        recovery = RuntimeSnapshotRecoveryResponse(
            stale_inflight=stale_recovery,
            snapshot_self_heal=snapshot_self_heal if snapshot_self_heal.repaired else None,
        )

    return ThreadRuntimeSnapshotResponse(
        thread_id=thread_id,
        runs=[_record_to_response(record) for record in records],
        run_messages=run_messages,
        rounds=rounds,
        task_lanes=task_lanes,
        recovery=recovery,
    )


@router.get("/{thread_id}/runs/{run_id}", response_model=RunResponse)
@require_permission("runs", "read", owner_check=True)
async def get_run(thread_id: str, run_id: str, request: Request) -> RunResponse:
    """Get details of a specific run."""
    record = await resolve_thread_run(thread_id, run_id, request)
    return _record_to_response(record)


@router.post("/{thread_id}/runs/{run_id}/cancel")
@require_permission("runs", "cancel", owner_check=True, require_existing=True)
async def cancel_run(
    thread_id: str,
    run_id: str,
    request: Request,
    wait: bool = Query(default=False, description="Block until run completes after cancel"),
    action: Literal["interrupt", "rollback"] = Query(default="interrupt", description="Cancel action"),
) -> Response:
    """Cancel a running or pending run.

    - action=interrupt: Stop execution, keep current checkpoint (can be resumed)
    - action=rollback: Stop execution, revert to pre-run checkpoint state
    - wait=true: Block until the run fully stops, return 204
    - wait=false: Return immediately with 202
    """
    run_mgr = get_run_manager(request)
    record = await resolve_thread_run(thread_id, run_id, request)

    cancelled = await run_mgr.cancel(run_id, action=action)
    if not cancelled:
        raise HTTPException(status_code=409, detail=_cancel_conflict_detail(run_id, record))

    if wait and record.task is not None:
        try:
            await _bounded_wait_for_cancelled_task(record.task)
        except HTTPException:
            raise
        return Response(status_code=204)

    return Response(status_code=202)


@router.get("/{thread_id}/runs/{run_id}/join")
@require_permission("runs", "read", owner_check=True)
async def join_run(thread_id: str, run_id: str, request: Request) -> StreamingResponse:
    """Join an existing run's SSE stream."""
    run_mgr = get_run_manager(request)
    record = await resolve_thread_run(thread_id, run_id, request)
    if record.store_only and is_inflight_status(record.status):
        raise HTTPException(status_code=409, detail=f"Run {run_id} is not active on this worker and cannot be streamed")

    bridge = get_stream_bridge(request)
    event_store = get_run_event_store(request)
    user_id = get_request_storage_user_id(request)
    return StreamingResponse(
        sse_consumer(bridge, record, request, run_mgr, event_store=event_store, user_id=user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Register GET and POST as separate routes so each method gets a unique OpenAPI
# operationId. ``api_route(methods=["GET", "POST"])`` shares one route registration
# across both methods, which makes FastAPI emit the same ``operationId`` twice and
# warn about a duplicate operation id during OpenAPI generation.
@router.get("/{thread_id}/runs/{run_id}/stream", response_model=None)
@router.post("/{thread_id}/runs/{run_id}/stream", response_model=None)
@require_permission("runs", "read", owner_check=True)
async def stream_existing_run(
    thread_id: str,
    run_id: str,
    request: Request,
    action: Literal["interrupt", "rollback"] | None = Query(default=None, description="Cancel action"),
    wait: int = Query(default=0, description="Block until cancelled (1) or return immediately (0)"),
):
    """Join an existing run's SSE stream (GET), or cancel-then-stream (POST).

    The LangGraph SDK's ``joinStream`` and ``useStream`` stop button both use
    ``POST`` to this endpoint.  When ``action=interrupt`` or ``action=rollback``
    is present the run is cancelled first; the response then streams any
    remaining buffered events so the client observes a clean shutdown.
    """
    run_mgr = get_run_manager(request)
    record = await resolve_thread_run(thread_id, run_id, request)
    if record.store_only and action is None and is_inflight_status(record.status):
        raise HTTPException(status_code=409, detail=f"Run {run_id} is not active on this worker and cannot be streamed")

    # Cancel if an action was requested (stop-button / interrupt flow)
    if action is not None:
        cancelled = await run_mgr.cancel(run_id, action=action)
        if not cancelled:
            raise HTTPException(status_code=409, detail=_cancel_conflict_detail(run_id, record))
        if wait and record.task is not None:
            try:
                await _bounded_wait_for_cancelled_task(record.task)
            except HTTPException:
                raise
            return Response(status_code=204)

    bridge = get_stream_bridge(request)
    event_store = get_run_event_store(request)
    user_id = get_request_storage_user_id(request)
    return StreamingResponse(
        sse_consumer(bridge, record, request, run_mgr, event_store=event_store, user_id=user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Messages / Events / Token usage endpoints
# ---------------------------------------------------------------------------


@router.get("/{thread_id}/messages")
@require_permission("runs", "read", owner_check=True)
async def list_thread_messages(
    thread_id: str,
    request: Request,
    limit: int = Query(default=50, le=200),
    before_seq: int | None = Query(default=None),
    after_seq: int | None = Query(default=None),
    run_id: str | None = Query(default=None),
) -> list[dict]:
    """Return displayable messages for a thread, optionally scoped to a single run, with feedback attached."""
    event_store = get_run_event_store(request)
    user_id = get_request_storage_user_id(request)
    scoped_run: RunRecord | None = None
    if run_id is not None:
        scoped_run = await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
        list_messages_kwargs: dict[str, Any] = {"limit": limit, "before_seq": before_seq, "after_seq": after_seq}
        if _supports_user_id_keyword(event_store.list_messages_by_run):
            list_messages_kwargs["user_id"] = user_id
        messages = await event_store.list_messages_by_run(thread_id, run_id, **list_messages_kwargs)
    else:
        list_messages_kwargs = {"limit": limit, "before_seq": before_seq, "after_seq": after_seq}
        if _supports_user_id_keyword(event_store.list_messages):
            list_messages_kwargs["user_id"] = user_id
        messages = await event_store.list_messages(thread_id, **list_messages_kwargs)
    attach_message_display(messages)

    # Find the last AI message per run_id. AI messages are persisted by
    # RunJournal with event_type "llm.ai.response" (see runtime/journal.py);
    # the event store returns that value verbatim, so match on it here.
    last_ai_per_run: dict[str, int] = {}  # run_id -> index in messages list
    for i, msg in enumerate(messages):
        if msg.get("event_type") == "llm.ai.response":
            last_ai_per_run[msg["run_id"]] = i

    # Attach feedback to the last AI message of each run. Only query when there
    # is an AI message to attach it to — threads with no completed AI turn yet
    # would otherwise pay for a grouped feedback lookup whose result is unused.
    feedback_map: dict[str, dict] = {}
    if last_ai_per_run:
        feedback_repo = get_feedback_repo(request)
        feedback_map = await feedback_repo.list_by_thread_grouped(thread_id, user_id=user_id)

    last_ai_indices = set(last_ai_per_run.values())
    for i, msg in enumerate(messages):
        if i in last_ai_indices:
            run_id = msg["run_id"]
            fb = feedback_map.get(run_id)
            msg["feedback"] = (
                {
                    "feedback_id": fb["feedback_id"],
                    "rating": fb["rating"],
                    "comment": fb.get("comment"),
                }
                if fb
                else None
            )
        else:
            msg["feedback"] = None

    if scoped_run is not None:
        runs = [scoped_run]
    else:
        run_mgr = get_run_manager(request)
        runs = await run_mgr.list_by_thread(thread_id, user_id=user_id)
    run_durations = compute_run_durations(runs)

    if run_durations:
        for msg in messages:
            content = msg.get("content", {})
            if isinstance(content, dict) and content.get("type") == "ai":
                rid = msg.get("run_id")
                if rid and rid in run_durations:
                    if "additional_kwargs" not in content:
                        content["additional_kwargs"] = {}
                    content["additional_kwargs"]["turn_duration"] = run_durations[rid]

    return messages


async def _run_messages_page(
    thread_id: str,
    run_id: str,
    record: RunRecord,
    request: Request,
    *,
    user_id: str,
    limit: int,
    before_seq: int | None = None,
    after_seq: int | None = None,
) -> dict[str, Any]:
    event_store = get_run_event_store(request)
    list_messages_kwargs: dict[str, Any] = {
        "limit": limit + 1,
        "before_seq": before_seq,
        "after_seq": after_seq,
    }
    if _supports_user_id_keyword(event_store.list_messages_by_run):
        list_messages_kwargs["user_id"] = user_id
    rows = await event_store.list_messages_by_run(
        thread_id,
        run_id,
        **list_messages_kwargs,
    )
    data, has_more = trim_run_message_page(rows, limit=limit, after_seq=after_seq)
    attach_message_display(data)

    if data:
        durations = compute_run_durations([record])
        duration = durations.get(run_id)
        if duration is not None:
            for msg in reversed(data):
                content = msg.get("content")
                metadata = msg.get("metadata", {})
                is_middleware = str(metadata.get("caller", "")).startswith("middleware:")
                if isinstance(content, dict) and content.get("type") == "ai" and not is_middleware:
                    if "additional_kwargs" not in content:
                        content["additional_kwargs"] = {}
                    content["additional_kwargs"]["turn_duration"] = duration

    return {"data": data, "has_more": has_more}


@router.get("/{thread_id}/runs/{run_id}/messages")
@require_permission("runs", "read", owner_check=True)
async def list_run_messages(
    thread_id: str,
    run_id: str,
    request: Request,
    limit: int = Query(default=50, le=200, ge=1),
    before_seq: int | None = Query(default=None),
    after_seq: int | None = Query(default=None),
) -> dict:
    """Return paginated messages for a specific run.

    Response: { data: [...], has_more: bool }
    """
    user_id = get_request_storage_user_id(request)
    record = await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    return await _run_messages_page(
        thread_id,
        run_id,
        record,
        request,
        user_id=user_id,
        limit=limit,
        before_seq=before_seq,
        after_seq=after_seq,
    )


@router.get("/{thread_id}/runs/{run_id}/evidence")
@require_permission("runs", "read", owner_check=True)
async def list_run_evidence(
    thread_id: str,
    run_id: str,
    request: Request,
    limit: int = Query(default=500, le=2000),
) -> dict[str, Any]:
    """Return AI-readable, redacted evidence refs derived from run events."""
    user_id = get_request_storage_user_id(request)
    record = await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    return await _run_evidence_payload(thread_id, run_id, record, request, user_id=user_id, limit=limit)


@router.get("/{thread_id}/runs/{run_id}/quality-context", response_model=RunQualityContextResponse)
@require_permission("runs", "read", owner_check=True)
async def get_run_quality_context(
    thread_id: str,
    run_id: str,
    request: Request,
    config: AppConfig = Depends(get_config),
    evidence_limit: int = Query(default=500, le=2000),
    signal_limit: int = Query(default=50, ge=1, le=200),
) -> RunQualityContextResponse:
    """Return AI-readable quality-loop context for a run."""
    user_id = get_request_storage_user_id(request)
    record = await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    evidence = await _run_evidence_payload(thread_id, run_id, record, request, user_id=user_id, limit=evidence_limit)
    round_state, task_lanes = await _round_quality_context(thread_id, record, request, user_id=user_id)
    signals = list_quality_signals(thread_id=thread_id, user_id=user_id, run_id=run_id, limit=signal_limit)
    return RunQualityContextResponse(
        thread_id=thread_id,
        run_id=run_id,
        round_id=record.round_id,
        round_state=round_state,
        handoffs=_handoffs_from_task_lanes(task_lanes),
        evidence=evidence,
        capability_snapshot=build_capability_snapshot(config, thread_id=thread_id, user_id=user_id),
        quality_signals=[QualitySignalResponse.model_validate(signal) for signal in signals],
        quality_signal_summary=_quality_signal_summary(signals),
        quality_verdict=None,
        auto_rework=False,
    )


@router.get("/{thread_id}/runs/{run_id}/chair-brief", response_model=ChairOperatingBriefResponse)
@require_permission("runs", "read", owner_check=True)
async def get_run_chair_brief(
    thread_id: str,
    run_id: str,
    request: Request,
    config: AppConfig = Depends(get_config),
    round_id: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    evidence_limit: int = Query(default=500, le=2000),
    source_limit: int = Query(default=50, ge=1, le=200),
) -> ChairOperatingBriefResponse:
    """Return a compact AI-readable read model for Chair/lead AI."""
    user_id = get_request_storage_user_id(request)
    record = await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    evidence = await _run_evidence_payload(thread_id, run_id, record, request, user_id=user_id, limit=evidence_limit)
    _, task_lanes = await _round_quality_context(thread_id, record, request, user_id=user_id)
    signals = list_quality_signals(thread_id=thread_id, user_id=user_id, run_id=run_id, round_id=round_id, task_id=task_id, limit=source_limit)
    invocations = list_review_invocations(thread_id=thread_id, user_id=user_id, run_id=run_id, round_id=round_id, task_id=task_id, limit=source_limit)
    proposals = list_account_proposals(thread_id=thread_id, user_id=user_id, run_id=run_id, round_id=round_id, task_id=task_id, limit=source_limit)
    decisions = list_account_decisions(thread_id=thread_id, user_id=user_id, run_id=run_id, limit=source_limit)
    brief = build_chair_operating_brief(
        thread_id=thread_id,
        run_id=run_id,
        round_id=round_id or record.round_id,
        task_id=task_id,
        filter_round_id=round_id,
        capability_snapshot=build_capability_snapshot(config, thread_id=thread_id, user_id=user_id),
        handoffs=_handoffs_from_task_lanes(task_lanes),
        evidence=evidence,
        quality_signals=signals,
        review_invocations=invocations,
        account_proposals=proposals,
        account_decisions=decisions,
    )
    return ChairOperatingBriefResponse.model_validate(brief.as_dict())


@router.post("/{thread_id}/runs/{run_id}/quality-signals", response_model=QualitySignalResponse)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def create_quality_signal(
    thread_id: str,
    run_id: str,
    request: Request,
    body: QualitySignalCreateRequest,
    config: AppConfig = Depends(get_config),
) -> QualitySignalResponse:
    """Save an AI-authored quality signal for Chair/lead AI review."""
    user_id = get_request_storage_user_id(request)
    record = await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    if body.round_id and record.round_id and body.round_id != record.round_id:
        raise HTTPException(status_code=400, detail="quality signal round_id does not match run round_id")
    snapshot_version = body.capability_snapshot_version
    if snapshot_version is None:
        snapshot = build_capability_snapshot(config, thread_id=thread_id, user_id=user_id)
        snapshot_version = snapshot.get("version") if isinstance(snapshot.get("version"), int) else None
    try:
        signal = build_quality_signal(
            thread_id=thread_id,
            run_id=run_id,
            round_id=body.round_id or record.round_id,
            task_id=body.task_id,
            author_role=body.author_role,
            recommendation=body.recommendation,
            rationale=body.rationale,
            evidence_refs=body.evidence_refs,
            capability_refs=body.capability_refs,
            capability_snapshot_version=snapshot_version,
            target_role=body.target_role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_quality_signal(signal, user_id=user_id)
    return QualitySignalResponse.model_validate(signal.as_dict())


@router.get("/{thread_id}/runs/{run_id}/account-proposals", response_model=list[AccountUpdateProposalResponse])
@require_permission("runs", "read", owner_check=True)
async def list_run_account_proposals(
    thread_id: str,
    run_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AccountUpdateProposalResponse]:
    """List AI-authored account update proposals for a run."""
    user_id = get_request_storage_user_id(request)
    await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    proposals = list_account_proposals(thread_id=thread_id, user_id=user_id, run_id=run_id, limit=limit)
    return [AccountUpdateProposalResponse.model_validate(proposal) for proposal in proposals]


@router.post("/{thread_id}/runs/{run_id}/account-proposals", response_model=AccountUpdateProposalResponse)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def create_account_update_proposal(
    thread_id: str,
    run_id: str,
    request: Request,
    body: AccountUpdateProposalCreateRequest,
) -> AccountUpdateProposalResponse:
    """Save an AI-authored governance account update proposal for Chair review."""
    user_id = get_request_storage_user_id(request)
    record = await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    if body.round_id and record.round_id and body.round_id != record.round_id:
        raise HTTPException(status_code=400, detail="account proposal round_id does not match run round_id")
    try:
        proposal = build_account_update_proposal(
            thread_id=thread_id,
            run_id=run_id,
            round_id=body.round_id or record.round_id,
            task_id=body.task_id,
            proposed_by_role=body.proposed_by_role,
            account_type=body.account_type,
            proposed_change=body.proposed_change,
            rationale=body.rationale,
            evidence_refs=body.evidence_refs,
            quality_signal_refs=body.quality_signal_refs,
            review_invocation_refs=body.review_invocation_refs,
            target_role=body.target_role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_account_update_proposal(proposal, user_id=user_id)
    return AccountUpdateProposalResponse.model_validate(proposal.as_dict())


@router.post("/{thread_id}/runs/{run_id}/account-decisions", response_model=AccountDecisionResponse)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def create_account_decision(
    thread_id: str,
    run_id: str,
    request: Request,
    body: AccountDecisionCreateRequest,
) -> AccountDecisionResponse:
    """Save an AI-authored Chair decision record for an account proposal."""
    user_id = get_request_storage_user_id(request)
    await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    proposals = list_account_proposals(thread_id=thread_id, user_id=user_id, run_id=run_id, limit=200)
    if _account_proposal_by_id(proposals, body.proposal_id) is None:
        raise HTTPException(status_code=404, detail=f"Account update proposal {body.proposal_id} not found")
    try:
        decision = build_account_decision(
            proposal_id=body.proposal_id,
            thread_id=thread_id,
            run_id=run_id,
            decided_by_role=body.decided_by_role,
            decision=body.decision,
            rationale=body.rationale,
            revised_change=body.revised_change,
            evidence_refs=body.evidence_refs,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_account_decision(decision, user_id=user_id)
    return AccountDecisionResponse.model_validate(decision.as_dict())


@router.get("/{thread_id}/runs/{run_id}/role-states", response_model=list[RoleStateResponse])
@require_permission("runs", "read", owner_check=True)
async def list_run_role_states(
    thread_id: str,
    run_id: str,
    request: Request,
    role_name: str | None = Query(default=None, min_length=1, max_length=64),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[RoleStateResponse]:
    """List latest Chair-accepted AI role state summaries for this thread."""
    user_id = get_request_storage_user_id(request)
    await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    states = list_role_states(thread_id=thread_id, user_id=user_id, role_name=role_name, limit=limit)
    return [RoleStateResponse.model_validate(state) for state in states]


@router.post("/{thread_id}/runs/{run_id}/role-states", response_model=RoleStateResponse)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def create_role_state(
    thread_id: str,
    run_id: str,
    request: Request,
    body: RoleStateCreateRequest,
) -> RoleStateResponse:
    """Save a Chair-accepted role memory summary without dispatching work."""
    user_id = get_request_storage_user_id(request)
    record = await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    if body.round_id and record.round_id and body.round_id != record.round_id:
        raise HTTPException(status_code=400, detail="role state round_id does not match run round_id")
    try:
        state = build_role_state(
            thread_id=thread_id,
            run_id=run_id,
            round_id=body.round_id or record.round_id,
            role_name=body.role_name,
            summary=body.summary,
            current_focus=body.current_focus,
            open_questions=body.open_questions,
            accepted_signals=body.accepted_signals,
            evidence_refs=body.evidence_refs,
            artifact_refs=body.artifact_refs,
            updated_by_role=body.updated_by_role,
            target_role=body.target_role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_role_state(state, user_id=user_id)
    return RoleStateResponse.model_validate(state.as_dict())


@router.get("/{thread_id}/runs/{run_id}/round-plans", response_model=list[RoundPlanResponse])
@require_permission("runs", "read", owner_check=True)
async def list_run_round_plans(thread_id: str, run_id: str, request: Request, round_id: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=200)) -> list[RoundPlanResponse]:
    user_id = get_request_storage_user_id(request)
    await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    return [RoundPlanResponse.model_validate(p) for p in list_round_plans(thread_id=thread_id, user_id=user_id, run_id=run_id, round_id=round_id, limit=limit)]


@router.get("/{thread_id}/runs/{run_id}/round-plan", response_model=RoundPlanResponse | None)
@require_permission("runs", "read", owner_check=True)
async def get_latest_run_round_plan(thread_id: str, run_id: str, request: Request, round_id: str | None = Query(default=None)) -> RoundPlanResponse | None:
    user_id = get_request_storage_user_id(request)
    await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    plan = latest_round_plan(thread_id=thread_id, user_id=user_id, run_id=run_id, round_id=round_id)
    return RoundPlanResponse.model_validate(plan) if plan else None


@router.post("/{thread_id}/runs/{run_id}/round-plans", response_model=RoundPlanResponse)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def create_run_round_plan(thread_id: str, run_id: str, request: Request, body: RoundPlanCreateRequest) -> RoundPlanResponse:
    user_id = get_request_storage_user_id(request)
    record = await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    if body.round_id and record.round_id and body.round_id != record.round_id:
        raise HTTPException(status_code=400, detail="round plan round_id does not match run round_id")
    try:
        plan = build_round_plan(
            thread_id=thread_id,
            run_id=run_id,
            round_id=body.round_id or record.round_id,
            goal=body.goal,
            boundary=body.boundary,
            evidence_standard=body.evidence_standard,
            capability_release=body.capability_release,
            status=body.status,
            dispatch_plan=body.dispatch_plan,
            planned_lanes=body.planned_lanes,
            created_by_role=body.created_by_role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_round_plan(plan, user_id=user_id)
    return RoundPlanResponse.model_validate(plan.as_dict())


@router.get("/{thread_id}/runs/{run_id}/planned-lanes", response_model=list[PlannedLaneResponse])
@require_permission("runs", "read", owner_check=True)
async def list_run_planned_lanes(
    thread_id: str, run_id: str, request: Request, round_id: str | None = Query(default=None), status: LaneStatus | None = Query(default=None), limit: int = Query(default=100, ge=1, le=200)
) -> list[PlannedLaneResponse]:
    user_id = get_request_storage_user_id(request)
    await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    return [PlannedLaneResponse.model_validate(lane) for lane in list_planned_lanes(thread_id=thread_id, user_id=user_id, run_id=run_id, round_id=round_id, status=status, limit=limit)]


@router.post("/{thread_id}/runs/{run_id}/planned-lanes", response_model=PlannedLaneResponse)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def create_run_planned_lane(thread_id: str, run_id: str, request: Request, body: PlannedLaneCreateRequest) -> PlannedLaneResponse:
    user_id = get_request_storage_user_id(request)
    record = await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    if body.round_id and record.round_id and body.round_id != record.round_id:
        raise HTTPException(status_code=400, detail="planned lane round_id does not match run round_id")
    try:
        lane = build_planned_lane(
            thread_id=thread_id,
            run_id=run_id,
            round_id=body.round_id or record.round_id,
            target_role=body.target_role,
            reason=body.reason,
            expected_evidence=body.expected_evidence,
            status=body.status,
            linked_task_id=body.linked_task_id,
            evidence_refs=body.evidence_refs,
            artifact_refs=body.artifact_refs,
            output_refs=body.output_refs,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_planned_lane(lane, user_id=user_id)
    return PlannedLaneResponse.model_validate(lane.as_dict())


@router.post("/{thread_id}/runs/{run_id}/planned-lanes/{lane_id}/status", response_model=PlannedLaneResponse)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def update_run_planned_lane_status(thread_id: str, run_id: str, lane_id: str, request: Request, body: PlannedLaneUpdateRequest) -> PlannedLaneResponse:
    user_id = get_request_storage_user_id(request)
    await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    try:
        lane = update_planned_lane_status(
            thread_id=thread_id, user_id=user_id, run_id=run_id, lane_id=lane_id, status=body.status, linked_task_id=body.linked_task_id, evidence_refs=body.evidence_refs, artifact_refs=body.artifact_refs, output_refs=body.output_refs
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if lane is None:
        raise HTTPException(status_code=404, detail=f"Planned lane {lane_id} not found")
    return PlannedLaneResponse.model_validate(lane.as_dict())


@router.get("/{thread_id}/runs/{run_id}/chair-decisions", response_model=list[ChairDecisionResponse])
@require_permission("runs", "read", owner_check=True)
async def list_run_chair_decisions(thread_id: str, run_id: str, request: Request, round_id: str | None = Query(default=None), limit: int = Query(default=100, ge=1, le=200)) -> list[ChairDecisionResponse]:
    user_id = get_request_storage_user_id(request)
    await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    return [ChairDecisionResponse.model_validate(d) for d in list_chair_decisions(thread_id=thread_id, user_id=user_id, run_id=run_id, round_id=round_id, limit=limit)]


@router.post("/{thread_id}/runs/{run_id}/chair-decisions", response_model=ChairDecisionResponse)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def create_run_chair_decision(thread_id: str, run_id: str, request: Request, body: ChairDecisionCreateRequest) -> ChairDecisionResponse:
    user_id = get_request_storage_user_id(request)
    record = await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    if body.round_id and record.round_id and body.round_id != record.round_id:
        raise HTTPException(status_code=400, detail="chair decision round_id does not match run round_id")
    try:
        decision = build_chair_decision(
            thread_id=thread_id,
            run_id=run_id,
            round_id=body.round_id or record.round_id,
            decision_type=body.decision_type,
            status=body.status,
            decision=body.decision,
            reason=body.reason,
            evidence_refs=body.evidence_refs,
            affected_lanes=body.affected_lanes,
            handoffs=body.handoffs,
            created_by_role=body.created_by_role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_chair_decision(decision, user_id=user_id)
    return ChairDecisionResponse.model_validate(decision.as_dict())


@router.get("/{thread_id}/runs/{run_id}/pending-handoffs", response_model=list[PendingHandoffResponse])
@require_permission("runs", "read", owner_check=True)
async def list_run_pending_handoffs(
    thread_id: str,
    run_id: str,
    request: Request,
    status: PendingHandoffStatus | None = Query(default="pending"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[PendingHandoffResponse]:
    """List AI-authored next-role handoff suggestions; this never dispatches them."""
    user_id = get_request_storage_user_id(request)
    await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    handoffs = list_pending_handoffs(thread_id=thread_id, user_id=user_id, run_id=run_id, status=status, limit=limit)
    return [PendingHandoffResponse.model_validate(handoff) for handoff in handoffs]


@router.post("/{thread_id}/runs/{run_id}/pending-handoffs/{handoff_id}/resolve", response_model=PendingHandoffResponse)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def resolve_run_pending_handoff(
    thread_id: str,
    run_id: str,
    handoff_id: str,
    request: Request,
    body: PendingHandoffResolveRequest,
) -> PendingHandoffResponse:
    """Mark an AI-authored handoff suggestion handled by Chair without running it."""
    user_id = get_request_storage_user_id(request)
    await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    try:
        resolved = resolve_pending_handoff(
            thread_id=thread_id,
            user_id=user_id,
            run_id=run_id,
            handoff_id=handoff_id,
            status=body.status,
            resolved_by_role=body.resolved_by_role,
            resolution_note=body.resolution_note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"Pending handoff {handoff_id} not found")
    return PendingHandoffResponse.model_validate(resolved.as_dict())


@router.get("/{thread_id}/runs/{run_id}/review-invocations", response_model=list[ReviewInvocationResponse])
@require_permission("runs", "read", owner_check=True)
async def list_run_review_invocations(
    thread_id: str,
    run_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ReviewInvocationResponse]:
    """List AI-authored review invocation records for a run."""
    user_id = get_request_storage_user_id(request)
    await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    invocations = list_review_invocations(thread_id=thread_id, user_id=user_id, run_id=run_id, limit=limit)
    return [ReviewInvocationResponse.model_validate(invocation) for invocation in invocations]


@router.post("/{thread_id}/runs/{run_id}/review-invocations", response_model=ReviewInvocationResponse)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def create_review_invocation(
    thread_id: str,
    run_id: str,
    request: Request,
    body: ReviewInvocationCreateRequest,
) -> ReviewInvocationResponse:
    """Save an AI-authored request for a review role to inspect a focused question."""
    user_id = get_request_storage_user_id(request)
    record = await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    if body.round_id and record.round_id and body.round_id != record.round_id:
        raise HTTPException(status_code=400, detail="review invocation round_id does not match run round_id")
    try:
        invocation = build_review_invocation(
            thread_id=thread_id,
            run_id=run_id,
            round_id=body.round_id or record.round_id,
            task_id=body.task_id,
            requested_by_role=body.requested_by_role,
            reviewer_role=body.reviewer_role,
            reason=body.reason,
            focus=body.focus,
            evidence_refs=body.evidence_refs,
            handoff_refs=body.handoff_refs,
            quality_signal_refs=body.quality_signal_refs,
            target_role=body.target_role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_review_invocation(invocation, user_id=user_id)
    return ReviewInvocationResponse.model_validate(invocation.as_dict())


@router.post("/{thread_id}/runs/{run_id}/review-invocations/{invocation_id}/complete", response_model=ReviewInvocationResponse)
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def complete_run_review_invocation(
    thread_id: str,
    run_id: str,
    invocation_id: str,
    request: Request,
    body: ReviewInvocationCompleteRequest,
) -> ReviewInvocationResponse:
    """Append the reviewer result summary for an existing invocation."""
    user_id = get_request_storage_user_id(request)
    await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    rows = list_review_invocations(thread_id=thread_id, user_id=user_id, run_id=run_id, limit=200)
    row = _review_invocation_by_id(rows, invocation_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Review invocation {invocation_id} not found")
    invocation = review_invocation_from_dict(row)
    if invocation.status != "requested":
        raise HTTPException(status_code=409, detail=f"Review invocation {invocation_id} is already {invocation.status}")
    try:
        completed = complete_review_invocation(invocation, result_summary=body.result_summary, result_evidence_refs=body.result_evidence_refs)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_review_invocation(completed, user_id=user_id)
    return ReviewInvocationResponse.model_validate(completed.as_dict())


@router.get("/{thread_id}/runs/{run_id}/events")
@require_permission("runs", "read", owner_check=True)
async def list_run_events(
    thread_id: str,
    run_id: str,
    request: Request,
    event_types: str | None = Query(default=None),
    limit: int = Query(default=500, le=2000),
    after_seq: int | None = Query(default=None),
) -> list[dict]:
    """Return the full event stream for a run (debug/audit)."""
    user_id = get_request_storage_user_id(request)
    await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    event_store = get_run_event_store(request)
    types = event_types.split(",") if event_types else None
    list_events_kwargs: dict[str, Any] = {"event_types": types, "limit": limit, "after_seq": after_seq}
    if _supports_user_id_keyword(event_store.list_events):
        list_events_kwargs["user_id"] = user_id
    return await event_store.list_events(thread_id, run_id, **list_events_kwargs)


@router.get("/{thread_id}/runs/{run_id}/artifacts")
@require_permission("runs", "read", owner_check=True)
async def list_run_artifacts(
    thread_id: str,
    run_id: str,
    request: Request,
    limit: int = Query(default=500, le=2000),
) -> list[dict[str, Any]]:
    """Return runtime-observed artifacts for a run."""
    user_id = get_request_storage_user_id(request)
    await _resolve_thread_run_for_user(thread_id, run_id, request, user_id=user_id)
    event_store = get_run_event_store(request)
    event_types = ["artifact.presented", "task_completed", "task_failed", "task_cancelled", "task_timed_out"]
    list_events_kwargs: dict[str, Any] = {"event_types": event_types, "limit": limit}
    if _supports_user_id_keyword(event_store.list_events):
        list_events_kwargs["user_id"] = user_id
    events = await event_store.list_events(thread_id, run_id, **list_events_kwargs)
    index = build_artifact_index(events)
    index = await asyncio.to_thread(_attach_artifact_file_metadata, index, thread_id, user_id=user_id)
    await _persist_artifact_index(request, index, user_id=user_id)
    return index


@router.get("/{thread_id}/token-usage", response_model=ThreadTokenUsageResponse)
@require_permission("threads", "read", owner_check=True)
async def thread_token_usage(
    thread_id: str,
    request: Request,
    include_active: bool = Query(default=False, description="Include running run progress snapshots"),
) -> ThreadTokenUsageResponse:
    """Thread-level token usage aggregation."""
    run_store = get_run_store(request)
    user_id = get_request_storage_user_id(request)
    if include_active:
        agg = await run_store.aggregate_tokens_by_thread(thread_id, include_active=True, user_id=user_id)
    else:
        agg = await run_store.aggregate_tokens_by_thread(thread_id, user_id=user_id)
    return ThreadTokenUsageResponse(thread_id=thread_id, **agg)


@router.get("/{thread_id}/context-usage", response_model=ThreadContextUsageResponse)
@require_permission("threads", "read", owner_check=True)
async def thread_context_usage(
    thread_id: str,
    request: Request,
    run_limit: int = Query(default=20, ge=1, le=50, description="Recent runs to scan for context snapshots"),
    limit: int = Query(default=20, ge=1, le=50, description="Recent context snapshots to return"),
) -> ThreadContextUsageResponse:
    """Latest model-input context estimates for a thread."""
    run_store = get_run_store(request)
    event_store = get_run_event_store(request)
    user_id = get_request_storage_user_id(request)
    runs = await run_store.list_by_thread(thread_id, user_id=user_id, limit=run_limit)

    snapshots: list[ThreadContextUsageSnapshot] = []
    for run in runs:
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            continue
        list_events_kwargs: dict[str, Any] = {"event_types": ["llm.context"], "limit": 200}
        if _supports_user_id_keyword(event_store.list_events):
            list_events_kwargs["user_id"] = user_id
        events = await event_store.list_events(thread_id, run_id, **list_events_kwargs)
        for event in events:
            snapshot = _context_usage_snapshot_from_event(event)
            if snapshot is not None:
                snapshots.append(snapshot)

    snapshots.sort(key=lambda item: (item.created_at, item.seq), reverse=True)
    recent = snapshots[:limit]
    by_caller: dict[str, ThreadContextUsageSnapshot] = {}
    for snapshot in snapshots:
        by_caller.setdefault(snapshot.caller, snapshot)

    return ThreadContextUsageResponse(
        thread_id=thread_id,
        latest=recent[0] if recent else None,
        latest_lead=by_caller.get("lead_agent"),
        by_caller=by_caller,
        recent=recent,
    )

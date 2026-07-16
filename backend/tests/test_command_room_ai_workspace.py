"""Command Room optional planning and delivery-loop workspace tests."""

from __future__ import annotations

import json

import pytest

from deerflow.command_room.ai_workspace import (
    AI_WORKSPACE_CONTEXT_HEADER,
    AI_WORKSPACE_FILES,
    accept_container_artifact,
    command_room_container_receipts_path,
    container_artifact_is_ai_authored,
    ensure_command_room_ai_workspace,
    format_ai_workspace_for_model,
    format_container_task_for_model,
    latest_project_lifecycle_status,
    prepare_command_room_container_task,
    record_container_task_completion,
    record_container_task_terminal,
    record_project_lifecycle_status,
)


def _complete(task, text: str = "AI-authored natural-language handoff") -> None:
    task.output_path.write_text(text, encoding="utf-8")
    assert container_artifact_is_ai_authored(task)
    assert record_container_task_completion(task)


def test_ai_workspace_creates_handoff_files_without_overwriting_ai_text(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1")

    for filename in AI_WORKSPACE_FILES:
        assert (root / filename).is_file()

    spec = root / "01-planning" / "spec.md"
    spec.write_text("Chair-approved plan.\n", encoding="utf-8")

    assert ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1") == root
    assert spec.read_text(encoding="utf-8") == "Chair-approved plan.\n"


def test_execution_cycle_labels_do_not_control_task_sequence(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1")

    execution_one = prepare_command_room_container_task(
        root,
        container="execution",
        task_id="executor-1",
        delivery_cycle_index=1,
    )
    assert execution_one.output_path.parent == root / "03-delivery" / "cycle-01" / "execution"
    _complete(execution_one)

    execution_two = prepare_command_room_container_task(
        root,
        container="execution",
        task_id="executor-2",
        delivery_cycle_index=2,
    )
    assert execution_two.output_path.parent == root / "03-delivery" / "cycle-02" / "execution"

    review_one = prepare_command_room_container_task(
        root,
        container="review",
        task_id="reviewer-1",
        delivery_cycle_index=1,
    )
    assert review_one.output_path.parent == root / "03-delivery" / "cycle-01" / "review"
    assert str(execution_one.output_path.parent) in format_container_task_for_model(review_one)
    _complete(review_one)

    assert str(review_one.output_path.parent) in format_container_task_for_model(execution_two)


def test_context_discovery_runs_in_parallel_before_a_context_snapshot_and_planning(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1")

    first = prepare_command_room_container_task(
        root,
        container="context",
        task_id="context-first",
        container_artifact="context-discovery",
        work_package_id="package-a",
    )
    second = prepare_command_room_container_task(
        root,
        container="context",
        task_id="context-second",
        container_artifact="context-discovery",
        work_package_id="package-a",
    )
    assert first.output_path != second.output_path
    assert first.output_path.parent == root / "packages" / "package-a" / "00-context" / "discovery"

    early_planning = prepare_command_room_container_task(
        root,
        container="planning",
        task_id="planning-before-context-snapshot",
        container_artifact="planning-forward",
        work_package_id="package-a",
    )
    _complete(early_planning, "Planning remains an AI choice")

    _complete(first, "First factual discovery")
    _complete(second, "Second factual discovery")
    snapshot = prepare_command_room_container_task(
        root,
        container="context",
        task_id="context-recorder",
        container_artifact="context",
        work_package_id="package-a",
    )
    assert snapshot.output_path == root / "packages" / "package-a" / "00-context" / "context.md"
    _complete(snapshot, "Chair-approved factual context snapshot")

    planning = prepare_command_room_container_task(
        root,
        container="planning",
        task_id="planning-after-context",
        container_artifact="planning-forward",
        work_package_id="package-a",
    )
    assert snapshot.output_path in planning.input_paths


def test_work_packages_isolate_paths_and_allow_next_package_context_during_execution(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1")

    execution = prepare_command_room_container_task(
        root,
        container="execution",
        task_id="package-a-execution",
        delivery_cycle_index=1,
        work_package_id="package-a",
    )
    discovery = prepare_command_room_container_task(
        root,
        container="context",
        task_id="package-b-context",
        container_artifact="context-discovery",
        work_package_id="package-b",
    )

    assert execution.output_path.is_relative_to(root / "packages" / "package-a")
    assert discovery.output_path.is_relative_to(root / "packages" / "package-b")
    assert execution.receipt_path != discovery.receipt_path
    assert execution.work_package_id == "package-a"
    assert discovery.work_package_id == "package-b"


def test_legacy_delivery_does_not_close_an_explicit_package_planning_or_technical_design(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1")
    prepare_command_room_container_task(
        root,
        container="execution",
        task_id="legacy-execution",
        delivery_cycle_index=1,
    )

    discovery = prepare_command_room_container_task(
        root,
        container="context",
        task_id="package-b-discovery",
        container_artifact="context-discovery",
        work_package_id="package-b",
    )
    _complete(discovery)
    context = prepare_command_room_container_task(
        root,
        container="context",
        task_id="package-b-context",
        container_artifact="context",
        work_package_id="package-b",
    )
    _complete(context)
    planning_forward = prepare_command_room_container_task(
        root,
        container="planning",
        task_id="package-b-planning-forward",
        container_artifact="planning-forward",
        work_package_id="package-b",
    )
    planning_opposition = prepare_command_room_container_task(
        root,
        container="planning",
        task_id="package-b-planning-opposition",
        container_artifact="planning-opposition",
        work_package_id="package-b",
    )
    _complete(planning_forward)
    _complete(planning_opposition)
    planning_spec = prepare_command_room_container_task(
        root,
        container="planning",
        task_id="package-b-planning-spec",
        container_artifact="spec",
        work_package_id="package-b",
    )
    _complete(planning_spec)
    technical_forward = prepare_command_room_container_task(
        root,
        container="technical-design",
        task_id="package-b-technical-forward",
        container_artifact="technical-forward",
        work_package_id="package-b",
    )
    technical_opposition = prepare_command_room_container_task(
        root,
        container="technical-design",
        task_id="package-b-technical-opposition",
        container_artifact="technical-opposition",
        work_package_id="package-b",
    )
    _complete(technical_forward)
    _complete(technical_opposition)
    technical_plan = prepare_command_room_container_task(
        root,
        container="technical-design",
        task_id="package-b-technical-plan",
        container_artifact="technical-plan",
        work_package_id="package-b",
    )

    assert technical_plan.output_path.is_relative_to(root / "packages" / "package-b")


def test_legacy_delivery_does_not_programmatically_block_later_planning(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1")
    prepare_command_room_container_task(
        root,
        container="execution",
        task_id="legacy-execution",
        delivery_cycle_index=1,
    )

    planning = prepare_command_room_container_task(
        root,
        container="planning",
        task_id="later-planning",
        container_artifact="planning-forward",
    )

    assert planning.output_path == root / "01-planning" / "forward.md"


def test_review_label_does_not_wait_for_execution_handoffs(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1")
    first = prepare_command_room_container_task(
        root,
        container="execution",
        task_id="execution-first",
        delivery_cycle_index=1,
        work_package_id="package-a",
    )
    second = prepare_command_room_container_task(
        root,
        container="execution",
        task_id="execution-second",
        delivery_cycle_index=1,
        work_package_id="package-a",
    )
    _complete(first)

    early_review = prepare_command_room_container_task(
        root,
        container="review",
        task_id="review-before-second-execution-finishes",
        delivery_cycle_index=1,
        work_package_id="package-a",
    )
    assert first.output_path.parent in early_review.input_paths

    _complete(second)
    review = prepare_command_room_container_task(
        root,
        container="review",
        task_id="review-after-all-execution",
        delivery_cycle_index=1,
        work_package_id="package-a",
    )
    assert first.output_path.parent in review.input_paths


def test_project_lifecycle_routes_steward_curators_and_final_governance_review(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "project-1")
    execution_one = prepare_command_room_container_task(
        root,
        container="execution",
        task_id="execution-1",
        delivery_cycle_index=1,
    )
    _complete(execution_one)
    review_one = prepare_command_room_container_task(
        root,
        container="review",
        task_id="review-1",
        delivery_cycle_index=1,
    )
    _complete(review_one)

    record_project_lifecycle_status(
        root,
        status="task_closed",
        summary="Review 1 accepted by Chair.",
        review_cycle_index=1,
    )
    execution_after_close = prepare_command_room_container_task(
        root,
        container="execution",
        task_id="execution-after-close-label",
        delivery_cycle_index=2,
    )
    assert execution_after_close.delivery_cycle_index == 2

    steward = prepare_command_room_container_task(
        root,
        container="project-steward",
        task_id="steward-1",
        delivery_cycle_index=1,
    )
    _complete(steward, "Project is substantively complete; curate closure work.")
    record_project_lifecycle_status(
        root,
        status="project_complete",
        summary="Chair accepts substantive project completion.",
    )

    debt = prepare_command_room_container_task(
        root,
        container="debt-curation",
        task_id="debt-1",
    )
    learning = prepare_command_room_container_task(
        root,
        container="learning-curation",
        task_id="learning-1",
    )
    _complete(debt, "Required debt updates.")
    _complete(learning, "Durable learning updates.")

    governance_execution = prepare_command_room_container_task(
        root,
        container="execution",
        task_id="execution-2",
        delivery_cycle_index=2,
    )
    _complete(governance_execution)
    governance_review = prepare_command_room_container_task(
        root,
        container="review",
        task_id="review-2",
        delivery_cycle_index=2,
    )
    _complete(governance_review)
    final = record_project_lifecycle_status(
        root,
        status="closed",
        summary="Chair accepts governance Review 2 and closes the project.",
    )

    assert final["status"] == "closed"
    assert latest_project_lifecycle_status(root)["status"] == "closed"


def test_optional_planning_uses_independent_angles_and_chair_spec(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1")

    forward = prepare_command_room_container_task(
        root,
        container="planning",
        task_id="planner-forward",
        container_artifact="planning-forward",
    )
    opposition = prepare_command_room_container_task(
        root,
        container="planning",
        task_id="planner-opposition",
        container_artifact="planning-opposition",
    )
    assert opposition.output_path != forward.output_path
    assert forward.output_path not in opposition.input_paths
    assert opposition.output_path not in forward.input_paths
    _complete(forward, "Forward planning angle")

    spec = prepare_command_room_container_task(
        root,
        container="planning",
        task_id="chair-plan-before-opposition-finishes",
        container_artifact="spec",
    )
    _complete(spec, "Chair direction can be recorded whenever the Chair chooses")

    _complete(opposition, "Opposite planning angle")
    assert spec.output_path == root / "01-planning" / "spec.md"
    assert forward.output_path in spec.input_paths
    assert opposition.output_path in spec.input_paths

    execution = prepare_command_room_container_task(
        root,
        container="execution",
        task_id="executor-1",
        delivery_cycle_index=1,
    )
    assert spec.output_path in execution.input_paths


def test_optional_technical_design_requires_two_angles_and_a_chair_plan(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1")

    forward = prepare_command_room_container_task(
        root,
        container="technical-design",
        task_id="technical-forward",
        container_artifact="technical-forward",
    )
    opposition = prepare_command_room_container_task(
        root,
        container="technical-design",
        task_id="technical-opposition",
        container_artifact="technical-opposition",
    )
    _complete(forward, "Forward technical design")
    _complete(opposition, "Opposite technical angle")

    design = prepare_command_room_container_task(
        root,
        container="technical-design",
        task_id="chair-design",
        container_artifact="technical-plan",
    )
    assert design.output_path == root / "02-technical-design" / "technical-plan.md"
    _complete(design, "Chair-approved technical plan")

    execution = prepare_command_room_container_task(
        root,
        container="execution",
        task_id="executor-1",
        delivery_cycle_index=1,
    )
    assert design.output_path in execution.input_paths


def test_started_optional_stage_does_not_programmatically_block_execution(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1")
    forward = prepare_command_room_container_task(
        root,
        container="planning",
        task_id="planner-forward",
        container_artifact="planning-forward",
    )
    _complete(forward)

    execution = prepare_command_room_container_task(
        root,
        container="execution",
        task_id="executor-1",
        delivery_cycle_index=1,
    )

    assert execution.output_path.parent == root / "03-delivery" / "cycle-01" / "execution"


def test_receipt_records_only_objective_handoff_facts(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1")
    execution = prepare_command_room_container_task(
        root,
        container="execution",
        task_id="executor-1",
        delivery_cycle_index=1,
    )
    _complete(execution, "Secret natural-language reasoning must stay out of receipts")

    receipt_path = command_room_container_receipts_path(root)
    assert not receipt_path.is_relative_to(root)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8").splitlines()[-1])
    assert receipt["container"] == "execution"
    assert receipt["delivery_cycle_index"] == 1
    assert receipt["artifact_sha256"]
    assert "Secret natural-language" not in json.dumps(receipt)


def test_terminal_handoff_status_does_not_become_a_review_gate(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1")
    execution = prepare_command_room_container_task(
        root,
        container="execution",
        task_id="executor-1",
        delivery_cycle_index=1,
    )
    record_container_task_terminal(execution, status="failed")

    review = prepare_command_room_container_task(
        root,
        container="review",
        task_id="reviewer-1",
        delivery_cycle_index=1,
    )
    assert review.artifact_kind == "findings"


def test_failed_optional_artifact_can_be_retried_when_no_handoff_was_written(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1")
    failed = prepare_command_room_container_task(
        root,
        container="technical-design",
        task_id="technical-opposition-failed",
        container_artifact="technical-opposition",
    )
    record_container_task_terminal(failed, status="failed")

    retry = prepare_command_room_container_task(
        root,
        container="technical-design",
        task_id="technical-opposition-retry",
        container_artifact="technical-opposition",
    )

    assert retry.task_id == "technical-opposition-retry"


def test_chair_can_accept_changed_optional_artifact_after_transport_failure(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1")
    forward = prepare_command_room_container_task(
        root,
        container="technical-design",
        task_id="technical-forward",
        container_artifact="technical-forward",
    )
    _complete(forward, "Forward technical design")
    opposition = prepare_command_room_container_task(
        root,
        container="technical-design",
        task_id="technical-opposition",
        container_artifact="technical-opposition",
    )
    opposition.output_path.write_text("Complete contrary technical angle", encoding="utf-8")
    record_container_task_terminal(opposition, status="failed")

    accepted = accept_container_artifact(root, artifact_kind="technical-opposition")

    assert accepted["status"] == "completed"
    assert accepted["accepted_by_chair"] is True
    design = prepare_command_room_container_task(
        root,
        container="technical-design",
        task_id="chair-design",
        container_artifact="technical-plan",
    )
    assert design.output_path == root / "02-technical-design" / "technical-plan.md"


def test_chair_acceptance_recovers_legacy_baseline_from_reservation(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1")
    opposition = prepare_command_room_container_task(
        root,
        container="technical-design",
        task_id="technical-opposition",
        container_artifact="technical-opposition",
    )
    opposition.output_path.write_text("Complete contrary technical angle", encoding="utf-8")
    record_container_task_terminal(opposition, status="failed")

    receipt_path = command_room_container_receipts_path(root)
    receipts = [json.loads(line) for line in receipt_path.read_text(encoding="utf-8").splitlines()]
    receipts[-1].pop("artifact_sha256_before")
    receipt_path.write_text("".join(json.dumps(receipt) + "\n" for receipt in receipts), encoding="utf-8")

    accepted = accept_container_artifact(root, artifact_kind="technical-opposition")

    assert accepted["artifact_sha256_before"] == receipts[-2]["artifact_sha256_before"]


def test_chair_acceptance_rejects_legacy_receipt_without_a_baseline(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1")
    opposition = prepare_command_room_container_task(
        root,
        container="technical-design",
        task_id="technical-opposition",
        container_artifact="technical-opposition",
    )
    opposition.output_path.write_text("Complete contrary technical angle", encoding="utf-8")
    record_container_task_terminal(opposition, status="failed")

    receipt_path = command_room_container_receipts_path(root)
    receipts = [json.loads(line) for line in receipt_path.read_text(encoding="utf-8").splitlines()]
    for receipt in receipts:
        receipt.pop("artifact_sha256_before", None)
    receipt_path.write_text("".join(json.dumps(receipt) + "\n" for receipt in receipts), encoding="utf-8")

    with pytest.raises(ValueError, match="no trustworthy pre-task hash"):
        accept_container_artifact(root, artifact_kind="technical-opposition")


def test_failed_fixed_governance_role_can_be_retried(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "project-1")
    execution = prepare_command_room_container_task(
        root,
        container="execution",
        task_id="execution-1",
        delivery_cycle_index=1,
    )
    _complete(execution)
    review = prepare_command_room_container_task(
        root,
        container="review",
        task_id="review-1",
        delivery_cycle_index=1,
    )
    _complete(review)
    record_project_lifecycle_status(
        root,
        status="task_closed",
        summary="Review 1 accepted by Chair.",
        review_cycle_index=1,
    )

    failed_steward = prepare_command_room_container_task(
        root,
        container="project-steward",
        task_id="steward-failed",
        delivery_cycle_index=1,
    )
    record_container_task_terminal(failed_steward, status="failed")

    retry = prepare_command_room_container_task(
        root,
        container="project-steward",
        task_id="steward-retry",
        delivery_cycle_index=1,
    )

    assert retry.task_id == "steward-retry"


def test_ai_workspace_context_describes_optional_labels_without_a_delivery_gate(tmp_path):
    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1")
    text = format_ai_workspace_for_model(root)

    assert text is not None
    assert AI_WORKSPACE_CONTEXT_HEADER in text
    assert str(root / "01-planning" / "spec.md") in text
    assert str(root / "02-technical-design" / "technical-plan.md") in text
    assert str(root / "03-delivery") in text
    assert "optional" in text.lower()
    assert "optional factual labels" in text
    assert "never authorize, block, sequence, or choose a task" in text
    assert "Execution N -> Review N" not in text
    assert "PASS/FAIL" not in text


def test_ai_workspace_rejects_unsafe_run_id_and_invalid_cycle(tmp_path):
    with pytest.raises(ValueError):
        ensure_command_room_ai_workspace(tmp_path / "workspace", "../run-1")

    root = ensure_command_room_ai_workspace(tmp_path / "workspace", "run-1")
    with pytest.raises(ValueError, match="positive integer"):
        prepare_command_room_container_task(
            root,
            container="execution",
            task_id="executor-zero",
            delivery_cycle_index=0,
        )

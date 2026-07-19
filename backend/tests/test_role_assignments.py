"""Tests for per-user professional-role model assignments."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from deerflow.config.paths import Paths
from deerflow.config.role_assignments import (
    RoleAssignment,
    RoleAssignments,
    load_role_assignments,
    save_role_assignments,
    update_role_assignment,
)


def test_role_assignments_round_trip_atomically(tmp_path):
    paths = Paths(base_dir=tmp_path)
    assignments = RoleAssignments(roles={"planner": RoleAssignment(model="gpt-5.6", reasoning_effort="max")})

    with patch("deerflow.config.role_assignments.get_paths", return_value=paths):
        save_role_assignments("alice", assignments)
        loaded = load_role_assignments("alice")

    assert loaded == assignments
    assert paths.user_role_assignments_file("alice").is_file()
    assert not list(paths.user_dir("alice").glob("*.tmp"))


def test_missing_role_assignments_returns_empty_config(tmp_path):
    paths = Paths(base_dir=tmp_path)

    with patch("deerflow.config.role_assignments.get_paths", return_value=paths):
        loaded = load_role_assignments("alice")

    assert loaded.roles == {}


def test_invalid_role_assignments_fall_back_without_breaking_role_list(tmp_path):
    paths = Paths(base_dir=tmp_path)
    path = paths.user_role_assignments_file("alice")
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")

    with patch("deerflow.config.role_assignments.get_paths", return_value=paths):
        loaded = load_role_assignments("alice")

    assert loaded.roles == {}


def test_concurrent_role_updates_preserve_each_role(tmp_path):
    paths = Paths(base_dir=tmp_path)

    def update(index: int) -> None:
        update_role_assignment(
            "alice",
            f"role-{index}",
            RoleAssignment(model="gpt-5.6", reasoning_effort="max"),
        )

    with patch("deerflow.config.role_assignments.get_paths", return_value=paths):
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(update, range(20)))
        loaded = load_role_assignments("alice")

    assert set(loaded.roles) == {f"role-{index}" for index in range(20)}

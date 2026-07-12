"""Contract tests for the sanitized local runtime value report."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def _load_report_module():
    script_path = Path(__file__).parents[1] / "scripts" / "runtime_value_report.py"
    spec = importlib.util.spec_from_file_location("runtime_value_report", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_runtime_fixture(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                user_id TEXT,
                status TEXT NOT NULL,
                total_input_tokens INTEGER NOT NULL,
                total_output_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                lead_agent_tokens INTEGER NOT NULL,
                subagent_tokens INTEGER NOT NULL,
                middleware_tokens INTEGER NOT NULL
            );
            CREATE TABLE task_lanes (
                thread_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                status TEXT NOT NULL,
                duration_ms INTEGER
            );
            CREATE TABLE artifact_provenance (
                id INTEGER PRIMARY KEY,
                user_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                virtual_path TEXT NOT NULL
            );
            CREATE TABLE feedback (
                feedback_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                user_id TEXT,
                rating INTEGER NOT NULL,
                comment TEXT
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO runs (
                run_id, thread_id, user_id, status,
                total_input_tokens, total_output_tokens, total_tokens,
                lead_agent_tokens, subagent_tokens, middleware_tokens
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("run-1", "thread-1", "user-1", "success", 30, 70, 100, 60, 40, 0),
                ("run-2", "thread-2", "user-2", "error", 20, 30, 50, 30, 20, 0),
            ],
        )
        connection.executemany(
            """
            INSERT INTO task_lanes (thread_id, run_id, task_id, status, duration_ms)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("thread-1", "run-1", "task-1", "completed", 1000),
                ("thread-2", "run-2", "task-2", "failed", 2500),
            ],
        )
        connection.execute(
            """
            INSERT INTO artifact_provenance (user_id, thread_id, run_id, virtual_path)
            VALUES (?, ?, ?, ?)
            """,
            ("user-1", "thread-1", "run-1", "/mnt/user-data/outputs/report.txt"),
        )
        connection.executemany(
            """
            INSERT INTO feedback (feedback_id, run_id, thread_id, user_id, rating, comment)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("feedback-1", "run-1", "thread-1", "user-1", 1, "Useful result"),
                ("feedback-2", "run-1", "thread-1", "user-2", 1, "Also useful"),
                ("feedback-3", "run-2", "thread-2", "user-2", -1, "Needs work"),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def test_runtime_value_report_aggregates_without_identifiers(tmp_path: Path) -> None:
    module = _load_report_module()
    database_path = tmp_path / "deerflow.db"
    _create_runtime_fixture(database_path)

    report = module.build_report(database_path)

    assert report["database"] == {
        "name": "deerflow.db",
        "tables": {
            "artifact_provenance": True,
            "feedback": True,
            "runs": True,
            "task_lanes": True,
        },
    }
    assert report["runs"] == {
        "available": True,
        "total": 2,
        "outcomes": {"error": 1, "success": 1},
    }
    assert report["tokens"] == {
        "available": True,
        "input": 50,
        "lead_agent": 90,
        "middleware": 0,
        "output": 100,
        "subagent": 60,
        "subagent_share": 0.4,
        "total": 150,
    }
    assert report["task_lanes"] == {
        "available": True,
        "duration_ms": {"p50": 1000, "p95": 2500},
        "outcomes": {"completed": 1, "failed": 1},
        "total": 2,
    }
    assert report["artifacts"] == {
        "available": True,
        "coverage": 0.5,
        "run_count": 1,
        "total": 1,
    }
    assert report["feedback"] == {
        "available": True,
        "coverage": 1.0,
        "negative": 1,
        "positive": 2,
        "run_count": 2,
        "total": 3,
    }

    rendered = json.dumps(report, sort_keys=True)
    assert "thread-1" not in rendered
    assert "user-1" not in rendered
    assert "report.txt" not in rendered
    assert "Useful result" not in rendered
    assert str(tmp_path) not in rendered


def test_runtime_value_report_handles_missing_optional_tables(tmp_path: Path) -> None:
    module = _load_report_module()
    database_path = tmp_path / "runs-only.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE runs (
                status TEXT NOT NULL,
                total_input_tokens INTEGER NOT NULL,
                total_output_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                lead_agent_tokens INTEGER NOT NULL,
                subagent_tokens INTEGER NOT NULL,
                middleware_tokens INTEGER NOT NULL
            );
            """
        )
        connection.commit()
    finally:
        connection.close()

    report = module.build_report(database_path)

    assert report["runs"]["available"] is True
    assert report["task_lanes"] == {
        "available": False,
        "duration_ms": {"p50": None, "p95": None},
        "outcomes": {},
        "total": 0,
    }
    assert report["artifacts"] == {
        "available": False,
        "coverage": None,
        "run_count": 0,
        "total": 0,
    }
    assert report["feedback"] == {
        "available": False,
        "coverage": None,
        "negative": 0,
        "positive": 0,
        "run_count": 0,
        "total": 0,
    }


def test_runtime_value_report_opens_the_database_read_only(tmp_path: Path) -> None:
    module = _load_report_module()
    database_path = tmp_path / "deerflow.db"
    _create_runtime_fixture(database_path)

    with module._open_read_only(database_path) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM runs")


def test_runtime_value_report_cli_emits_only_aggregate_json(tmp_path: Path) -> None:
    database_path = tmp_path / "deerflow.db"
    _create_runtime_fixture(database_path)
    script_path = Path(__file__).parents[1] / "scripts" / "runtime_value_report.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--db",
            str(database_path),
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(completed.stdout)
    assert report["runs"]["outcomes"] == {"error": 1, "success": 1}
    assert report["feedback"] == {
        "available": True,
        "coverage": 1.0,
        "negative": 1,
        "positive": 2,
        "run_count": 2,
        "total": 3,
    }
    assert "thread-1" not in completed.stdout
    assert "report.txt" not in completed.stdout
    assert "Useful result" not in completed.stdout

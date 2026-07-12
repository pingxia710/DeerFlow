"""Emit sanitized, read-only aggregates from a DeerFlow SQLite runtime database."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path

_REPORT_TABLES = ("runs", "task_lanes", "artifact_provenance")
_TOKEN_COLUMNS = {
    "input": "total_input_tokens",
    "output": "total_output_tokens",
    "total": "total_tokens",
    "lead_agent": "lead_agent_tokens",
    "subagent": "subagent_tokens",
    "middleware": "middleware_tokens",
}


def _open_read_only(database_path: Path) -> sqlite3.Connection:
    resolved = database_path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {database_path.name}")
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _available_tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows if row["name"] in _REPORT_TABLES}


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}


def _outcomes(
    connection: sqlite3.Connection,
    table: str,
    columns: set[str],
) -> dict[str, int]:
    if "status" not in columns:
        return {}
    rows = connection.execute(f"SELECT status, COUNT(*) AS count FROM {table} WHERE status IS NOT NULL GROUP BY status ORDER BY status").fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _empty_task_lanes() -> dict[str, object]:
    return {
        "available": False,
        "total": 0,
        "outcomes": {},
        "duration_ms": {"p50": None, "p95": None},
    }


def _empty_artifacts() -> dict[str, object]:
    return {"available": False, "total": 0, "run_count": 0, "coverage": None}


def build_report(database_path: Path) -> dict[str, object]:
    """Build aggregate-only metrics without exposing runtime record contents."""
    with _open_read_only(database_path) as connection:
        available_tables = _available_tables(connection)
        table_flags = {table: table in available_tables for table in sorted(_REPORT_TABLES)}

        runs: dict[str, object] = {"available": False, "total": 0, "outcomes": {}}
        tokens: dict[str, object] = {
            "available": False,
            **{name: 0 for name in _TOKEN_COLUMNS},
            "subagent_share": None,
        }
        if "runs" in available_tables:
            run_columns = _table_columns(connection, "runs")
            total = int(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
            runs = {
                "available": True,
                "total": total,
                "outcomes": _outcomes(connection, "runs", run_columns),
            }
            available_token_columns = {name: column for name, column in _TOKEN_COLUMNS.items() if column in run_columns}
            token_values = {name: 0 for name in _TOKEN_COLUMNS}
            if available_token_columns:
                columns_sql = ", ".join(f"COALESCE(SUM({column}), 0) AS {name}" for name, column in available_token_columns.items())
                row = connection.execute(f"SELECT {columns_sql} FROM runs").fetchone()
                token_values.update({name: int(row[name]) for name in available_token_columns})
            token_total = token_values["total"]
            tokens = {
                "available": True,
                **token_values,
                "subagent_share": (round(token_values["subagent"] / token_total, 4) if token_total > 0 else None),
            }

        task_lanes = _empty_task_lanes()
        if "task_lanes" in available_tables:
            lane_columns = _table_columns(connection, "task_lanes")
            total = int(connection.execute("SELECT COUNT(*) FROM task_lanes").fetchone()[0])
            durations: list[int] = []
            if "duration_ms" in lane_columns:
                durations = [int(row["duration_ms"]) for row in connection.execute("SELECT duration_ms FROM task_lanes WHERE duration_ms IS NOT NULL AND duration_ms >= 0")]
            task_lanes = {
                "available": True,
                "total": total,
                "outcomes": _outcomes(connection, "task_lanes", lane_columns),
                "duration_ms": {
                    "p50": _percentile(durations, 0.5),
                    "p95": _percentile(durations, 0.95),
                },
            }

        artifacts = _empty_artifacts()
        if "artifact_provenance" in available_tables:
            artifact_columns = _table_columns(connection, "artifact_provenance")
            total = int(connection.execute("SELECT COUNT(*) FROM artifact_provenance").fetchone()[0])
            run_count = int(connection.execute("SELECT COUNT(DISTINCT run_id) FROM artifact_provenance").fetchone()[0]) if "run_id" in artifact_columns else 0
            run_total = int(runs["total"])
            artifacts = {
                "available": True,
                "total": total,
                "run_count": run_count,
                "coverage": round(run_count / run_total, 4) if run_total > 0 else None,
            }

    return {
        "database": {"name": database_path.name, "tables": table_flags},
        "runs": runs,
        "tokens": tokens,
        "task_lanes": task_lanes,
        "artifacts": artifacts,
    }


def _format_outcomes(outcomes: object) -> str:
    if not isinstance(outcomes, dict) or not outcomes:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in outcomes.items())


def format_text(report: dict[str, object]) -> str:
    """Render the same aggregate-only report for a terminal operator."""
    database = report["database"]
    runs = report["runs"]
    tokens = report["tokens"]
    task_lanes = report["task_lanes"]
    artifacts = report["artifacts"]
    assert isinstance(database, dict)
    assert isinstance(runs, dict)
    assert isinstance(tokens, dict)
    assert isinstance(task_lanes, dict)
    assert isinstance(artifacts, dict)
    duration_ms = task_lanes["duration_ms"]
    assert isinstance(duration_ms, dict)
    return "\n".join(
        [
            f"Database: {database['name']}",
            f"Runs: {runs['total']} ({_format_outcomes(runs['outcomes'])})",
            f"Tokens: total={tokens['total']}, lead_agent={tokens['lead_agent']}, subagent={tokens['subagent']}, subagent_share={tokens['subagent_share']}",
            f"Task lanes: {task_lanes['total']} ({_format_outcomes(task_lanes['outcomes'])}), p50_ms={duration_ms['p50']}, p95_ms={duration_ms['p95']}",
            f"Artifacts: total={artifacts['total']}, run_coverage={artifacts['coverage']}",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="Path to a local SQLite database")
    parser.add_argument("--format", choices=("json", "text"), default="text")
    args = parser.parse_args()

    report = build_report(args.db)
    if args.format == "json":
        print(json.dumps(report, sort_keys=True))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Print read-only local delivery facts for the current Git checkout.

This command never fetches, checks out, merges, cleans, or deletes Git state.
It reports facts only; a human still decides whether a branch is ready.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def count_dirty_entries(status: str) -> int:
    """Count Git status entries without retaining their file paths."""
    return sum(1 for line in status.splitlines() if line)


def summarize_worktrees(porcelain: str) -> dict[str, int]:
    """Count attached and detached worktrees from Git porcelain output."""
    entries = [entry for entry in porcelain.strip().split("\n\n") if entry.startswith("worktree ")]
    detached = sum("detached" in entry.splitlines() for entry in entries)
    return {"total": len(entries), "attached": len(entries) - detached, "detached": detached}


def _git(repo: Path, *args: str, optional: bool = False) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode == 0:
        return completed.stdout
    if optional:
        return None
    detail = (completed.stderr or completed.stdout).strip()
    raise RuntimeError(detail or f"git {' '.join(args)} failed")


def _relation(repo: Path, base: str, head: str) -> dict[str, int] | None:
    output = _git(repo, "rev-list", "--left-right", "--count", f"{base}...{head}", optional=True)
    if output is None:
        return None
    try:
        behind, ahead = (int(value) for value in output.split())
    except ValueError as exc:
        raise RuntimeError(f"Unexpected git rev-list output: {output!r}") from exc
    return {"ahead": ahead, "behind": behind}


def build_report(repo: Path) -> dict[str, object]:
    """Collect aggregate delivery facts without mutating the repository."""
    root_output = _git(repo, "rev-parse", "--show-toplevel")
    assert root_output is not None
    root = Path(root_output.strip())
    branch_output = _git(root, "branch", "--show-current")
    status_output = _git(root, "status", "--porcelain")
    worktree_output = _git(root, "worktree", "list", "--porcelain")
    assert branch_output is not None and status_output is not None and worktree_output is not None

    branch = branch_output.strip() or None
    return {
        "branch": branch,
        "candidate_vs_main": _relation(root, "main", "HEAD"),
        "main_vs_origin": _relation(root, "origin/main", "main"),
        "workspace": {
            "dirty_entries": count_dirty_entries(status_output),
            "worktrees": summarize_worktrees(worktree_output),
        },
    }


def _format_relation(label: str, relation: object) -> str:
    if not isinstance(relation, dict):
        return f"{label}: unavailable"
    return f"{label}: ahead={relation['ahead']}, behind={relation['behind']}"


def format_text(report: dict[str, object]) -> str:
    """Render facts without paths, file names, or a readiness verdict."""
    workspace = report["workspace"]
    assert isinstance(workspace, dict)
    worktrees = workspace["worktrees"]
    assert isinstance(worktrees, dict)
    branch = report["branch"] or "detached HEAD"
    return "\n".join(
        [
            f"Branch: {branch}",
            _format_relation("Candidate vs main", report["candidate_vs_main"]),
            _format_relation("Local main vs origin/main", report["main_vs_origin"]),
            f"Current workspace: dirty_entries={workspace['dirty_entries']}",
            f"Registered worktrees: total={worktrees['total']}, attached={worktrees['attached']}, detached={worktrees['detached']}",
            "Advisory only: no fetch, checkout, merge, clean, or deletion was performed.",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Git checkout to inspect")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()

    report = build_report(args.repo)
    if args.format == "json":
        print(json.dumps(report, sort_keys=True))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

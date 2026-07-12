"""Regression checks for the read-only delivery readiness report."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def _load_module():
    script_path = Path(__file__).parents[1] / "scripts" / "delivery_readiness.py"
    spec = importlib.util.spec_from_file_location("delivery_readiness", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DeliveryReadinessTests(unittest.TestCase):
    def test_worktree_summary_distinguishes_detached_entries(self) -> None:
        module = _load_module()

        summary = module.summarize_worktrees(
            """worktree /repo/main
HEAD 1111111111111111111111111111111111111111
branch refs/heads/main

worktree /repo/feature
HEAD 2222222222222222222222222222222222222222
branch refs/heads/feature

worktree /repo/inspection
HEAD 3333333333333333333333333333333333333333
detached
"""
        )

        self.assertEqual(summary, {"total": 3, "attached": 2, "detached": 1})

    def test_dirty_count_never_retains_status_paths(self) -> None:
        module = _load_module()

        self.assertEqual(
            module.count_dirty_entries(" M config.yaml\n?? private/customer-export.csv\n"),
            2,
        )


if __name__ == "__main__":
    unittest.main()

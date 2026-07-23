#!/usr/bin/env python3
"""Capture a read-only Command Room run to check the direct-answer fast path.

The expected good behavior for a read-only discovery question is: the Chair
answers in the same run with its read-only tools (`ls`, `read_file`, `glob`,
`grep`) and makes zero `record_goal_workspace` and zero `task` calls. This
probe only captures the factual tool-call trace; pass the result to an
independent review AI for judgment.
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

from deerflow.client import DeerFlowClient

PROMPT = """\
这是一次只读 dry-run，用来观察 Command Room 的只读快速通道。不要修改文件，不要运行写入命令，不要派发任何子任务。

用户说：帮我看看 DeerFlow 项目仓库根目录的 Progress.md 最新一条记录讲了什么，直接告诉我。

这是只读查看：直接用你的只读工具读文件并在本轮回答；不要记录 Goal Mandate/Brief/Map，不要 Opposition 子任务，不要确认暂停。
""".strip()


def _run_probe(thread_id: str) -> dict[str, Any]:
    client = DeerFlowClient(agent_name="command-room", subagent_enabled=True, thinking_enabled=False)
    tool_calls: list[dict[str, Any]] = []
    ai_chunks: dict[str, list[str]] = {}
    last_ai_id = ""
    usage: dict[str, int] | None = None

    for event in client.stream(PROMPT, thread_id=thread_id, recursion_limit=80):
        if event.type == "messages-tuple":
            data = event.data
            if data.get("type") != "ai":
                continue
            msg_id = data.get("id") or ""
            content = data.get("content") or ""
            if content:
                ai_chunks.setdefault(msg_id, []).append(content)
                last_ai_id = msg_id
            for call in data.get("tool_calls") or []:
                tool_calls.append(call)
                if call.get("name") == "task":
                    args = call.get("args") or {}
                    print(
                        f"task: {args.get('subagent_type')} - {args.get('description')}",
                        flush=True,
                    )
        elif event.type == "end":
            usage = event.data.get("usage")

    tool_names = [str(call.get("name") or "") for call in tool_calls]
    return {
        "thread_id": thread_id,
        "tool_names": tool_names,
        "task_types": [str((call.get("args") or {}).get("subagent_type") or "") for call in tool_calls if call.get("name") == "task"],
        "record_goal_workspace_calls": sum(1 for name in tool_names if name == "record_goal_workspace"),
        "task_calls": sum(1 for name in tool_names if name == "task"),
        "read_tool_calls": sum(1 for name in tool_names if name in {"ls", "read_file", "glob", "grep"}),
        "usage": usage,
        "final_text": "".join(ai_chunks.get(last_ai_id, [])),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--thread-id",
        default=f"readonly-probe-{int(time.time())}",
        help="Thread id to use for the dry run. Defaults to a timestamped id.",
    )
    args = parser.parse_args(argv)

    print(json.dumps(_run_probe(args.thread_id), ensure_ascii=False, indent=2))
    print("command-room capture complete; pass this natural result to an independent review AI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

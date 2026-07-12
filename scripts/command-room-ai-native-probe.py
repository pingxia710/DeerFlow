#!/usr/bin/env python3
"""Optional read-only probe for goal-first Command Room behavior.

The probe observes whether Command Room starts safe discovery without imposing
a fixed role, round, evidence, or review workflow. It is a development probe,
not a runtime gate.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from deerflow.client import DeerFlowClient

PROMPT = """\
这是一次只读 dry-run，用来观察 Command Room 是否按目标直接推进。不要修改文件，不要运行写入命令，不要触碰生产系统。

用户说：我需要继续处理 Naxus 项目。先定位项目和当前状态，再告诉我最有价值的下一步。

目前没有提供项目目录、GitHub URL、压缩包或任务系统通道。

请自行选择最小的安全发现动作：可以直接使用可用工具，也可以在确有价值时委派一个助手。不要先让用户充当项目定位器；不要把普通发现任务变成固定的轮次、证据、反方、验收或任务管理流程。若安全发现仍无法推进，再自然说明缺少什么。
""".strip()

FORBIDDEN_DEFERRALS = (
    "下一轮如果继续",
    "如果继续",
    "建议直接深挖",
    "建议深挖",
    "继续的话",
    "if we continue",
    "if you want me to continue",
    "next turn",
    "suggest digging into",
)
FORBIDDEN_LABELS = ("VERDICT:", "ROUND CARD", "EVIDENCE SIGNAL")


def _task_types(calls: list[dict[str, Any]]) -> list[str]:
    return [str((call.get("args") or {}).get("subagent_type") or "") for call in calls if call.get("name") == "task"]


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
                    print(f"task: {args.get('subagent_type')} - {args.get('description')}", flush=True)
        elif event.type == "end":
            usage = event.data.get("usage")

    tool_names = [str(call.get("name") or "") for call in tool_calls]
    first_tool = tool_names[0] if tool_names else ""
    first_discovery_index = next((index for index, name in enumerate(tool_names) if name != "ask_clarification"), None)
    first_clarification_index = next((index for index, name in enumerate(tool_names) if name == "ask_clarification"), None)
    clarification_before_discovery = first_clarification_index is not None and (first_discovery_index is None or first_clarification_index < first_discovery_index)
    final_text = "".join(ai_chunks.get(last_ai_id, []))
    final_upper = final_text.upper()
    final_lower = final_text.lower()
    task_types = _task_types(tool_calls)

    checks = {
        "starts_safe_discovery": bool(tool_names) and first_tool != "ask_clarification",
        "no_clarification_before_discovery": not clarification_before_discovery,
        "no_human_todo_tool": "write_todos" not in tool_names,
        "no_default_opposition": "opposition" not in task_types,
        "no_visible_process_labels": not any(label in final_upper for label in FORBIDDEN_LABELS),
        "no_next_round_deferral": not any(token in final_lower for token in FORBIDDEN_DEFERRALS),
    }

    return {
        "thread_id": thread_id,
        "checks": checks,
        "ok": all(checks.values()),
        "tool_names": tool_names,
        "task_types": task_types,
        "usage": usage,
        "final_text_chars": len(final_text),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--thread-id",
        default=f"ai-native-probe-{int(time.time())}",
        help="Thread id to use for the dry run. Defaults to a timestamped id.",
    )
    args = parser.parse_args(argv)

    result = _run_probe(args.thread_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result["ok"]:
        print("command-room goal-first probe: observed expected behavior")
        return 0

    print("command-room goal-first probe: expected behavior not observed", file=sys.stderr)
    failed = [name for name, ok in result["checks"].items() if not ok]
    print("failed checks: " + ", ".join(failed), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

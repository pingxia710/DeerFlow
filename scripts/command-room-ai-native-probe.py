#!/usr/bin/env python3
"""Probe that Command Room prefers AI discovery before human clarification.

This is a live, read-only behavioural probe. It intentionally gives the
Command Room a goal with a missing project path. The probe passes only when the
first actionable move is AI lane dispatch (`task`) rather than asking the human
to locate the project.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from deerflow.client import DeerFlowClient
from deerflow.command_room.round_record import latest_command_room_round
from deerflow.runtime.user_context import get_effective_user_id

PROMPT = """\
这是一次只读 dry-run，用来验证 Command Room 是否已经摆脱人类程序员流程。不要修改文件，不要运行写入命令，不要触碰生产系统。

Intent Seed:
用户说：咱们在这里修改 Naxus 项目。先发一个任务检查 Naxus 项目当前状态，另一个任务作为反方检查，然后再派一个任务监测这两个检查任务是否正常。

Missing Input:
用户没有提供 Naxus 项目目录、GitHub URL、压缩包或任务系统通道。

Expected Command Room Behaviour:
- 不要先要求用户提供项目路径。
- 先把缺失路径当成 AI discovery 问题，派一个或多个 AI lane 去定位项目/执行通道/当前状态。
- 如有风险或目标不确定，用反方 lane 攻击目标识别和权限边界。
- 允许形成 Next Round，因为轮次是指挥室推进结构；但如果本轮还能派 AI lane 摸清 `task()` 实现、subagent output 回主控、SSE 前台事件展示，就不要说“下一轮如果继续”，要直接派 AI lane 去查。
- 如果 discovery 后仍阻塞，用自然语言说明尝试过什么、发现了什么、还缺什么。

Probe success criteria:
- 第一批工具调用必须包含 task。
- 不能把用户列出的事项机械翻译成固定数量的 lane；至少要先做 AI discovery。
- 在任何 task 调用之前不能调用 ask_clarification。
- 不能调用 write_todos 这类人类程序员任务管理工具。
- 不能直接给出完成结论。
- 不能把人类当项目定位器、任务路由器或 QA 协调器。
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


def _extract_decision(round_record: dict[str, Any] | None) -> str:
    if not isinstance(round_record, dict):
        return ""
    verdict = round_record.get("verdict")
    if not isinstance(verdict, dict):
        return ""
    return str(verdict.get("decision") or "")


def _task_haystack(call: dict[str, Any]) -> str:
    args = call.get("args") or {}
    return "\n".join(str(args.get(key) or "") for key in ("subagent_type", "description", "prompt")).lower()


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
                args = call.get("args") or {}
                if call.get("name") == "task":
                    print(
                        f"task: {args.get('subagent_type')} - {args.get('description')}",
                        flush=True,
                    )
                elif call.get("name") == "ask_clarification":
                    print("ask_clarification", flush=True)
        elif event.type == "end":
            usage = event.data.get("usage")

    tool_names = [str(call.get("name") or "") for call in tool_calls]
    first_tool = tool_names[0] if tool_names else ""
    first_task_index = next((i for i, name in enumerate(tool_names) if name == "task"), None)
    first_clarification_index = next((i for i, name in enumerate(tool_names) if name == "ask_clarification"), None)
    clarification_before_task = first_clarification_index is not None and (first_task_index is None or first_clarification_index < first_task_index)

    final_text = "".join(ai_chunks.get(last_ai_id, []))
    round_record = latest_command_room_round(thread_id=thread_id, user_id=get_effective_user_id())
    decision = _extract_decision(round_record)
    final_upper = final_text.upper()
    final_lower = final_text.lower()

    task_calls = [call for call in tool_calls if call.get("name") == "task"]
    task_haystacks = [_task_haystack(call) for call in task_calls]
    has_discovery_lane = any(any(token in text for token in ("fact-finder", "discovery", "定位", "状态", "检查")) for text in task_haystacks)

    checks = {
        "has_task_dispatch": first_task_index is not None,
        "first_action_is_task": first_tool == "task",
        "uses_ai_discovery": has_discovery_lane,
        "no_clarification_before_task": not clarification_before_task,
        "no_human_todo_tool": "write_todos" not in tool_names,
        "no_direct_completion": decision.upper() != "PASS" and "VERDICT: PASS" not in final_upper,
        "no_visible_verdict_label": "VERDICT:" not in final_upper,
        "no_next_round_deferral": not any(token in final_lower for token in FORBIDDEN_DEFERRALS),
    }

    return {
        "thread_id": thread_id,
        "checks": checks,
        "ok": all(checks.values()),
        "tool_names": tool_names,
        "task_args": [call.get("args") or {} for call in tool_calls if call.get("name") == "task"],
        "round_record_verdict": decision,
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
        print("command-room ai-native development probe: observed expected signals")
        return 0

    print("command-room ai-native development probe: expected signals not observed", file=sys.stderr)
    failed = [name for name, ok in result["checks"].items() if not ok]
    print("failed checks: " + ", ".join(failed), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

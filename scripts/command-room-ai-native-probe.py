#!/usr/bin/env python3
"""Capture a read-only Command Room run for review by another AI."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

from deerflow.client import DeerFlowClient

PROMPT = """\
这是一次只读 dry-run，用来观察 Command Room 是否按 AI-AI-AI 推进。不要修改文件，不要运行写入命令，不要触碰生产系统。

用户说：我需要继续处理 Naxus 项目。先定位项目和当前状态，再告诉我最有价值的下一步。

目前没有提供项目目录、GitHub URL、压缩包或任务系统通道。

把人的兴趣、方向、非目标、现实权限和返回讨论边界记录为 Goal Mandate。指挥室保留方案、进度和最终判断。
对于这项实质性工作，由指挥室自己形成完整方案；方案形成后，把原始目标、事实、边界、标准和完整方案交给 opposition 跑一轮，再由指挥室合成定案。
不等待逐计划人工确认；在 Goal Mandate 和只读权限内继续调查与执行。只有需要改变方向、扩大权限或越过已定边界时才回到人讨论。
子任务结果是继续推进方案的事实，不做任务验收；方案达到完成标准即完成。
所有子AI返回完整自然语言结果后结束，程序不决定角色、质量、阶段或下一步。
若安全发现仍无法推进，再自然说明缺少什么。
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

    return {
        "thread_id": thread_id,
        "tool_names": [str(call.get("name") or "") for call in tool_calls],
        "task_types": [str((call.get("args") or {}).get("subagent_type") or "") for call in tool_calls if call.get("name") == "task"],
        "usage": usage,
        "final_text": "".join(ai_chunks.get(last_ai_id, [])),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--thread-id",
        default=f"ai-native-probe-{int(time.time())}",
        help="Thread id to use for the dry run. Defaults to a timestamped id.",
    )
    args = parser.parse_args(argv)

    print(json.dumps(_run_probe(args.thread_id), ensure_ascii=False, indent=2))
    print("command-room capture complete; pass this natural result to an independent review AI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

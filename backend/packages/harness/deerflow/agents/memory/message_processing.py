"""Shared helpers for turning conversations into memory update inputs."""

from __future__ import annotations

import re
from copy import copy
from typing import Any

_UPLOAD_BLOCK_RE = re.compile(r"<uploaded_files>[\s\S]*?</uploaded_files>\n*", re.IGNORECASE)
_ACTIVE_EXECUTION_HARD_PATTERNS = (
    re.compile(r"\bP\d+-[A-Z]\b"),
    re.compile(r"\bFORCED STOP\b", re.IGNORECASE),
    re.compile(r"\btool[-\s]?trace\b", re.IGNORECASE),
    re.compile(r"\btask[-\s]?trace\b", re.IGNORECASE),
    re.compile(r"\bsubagent\s+handoff\b", re.IGNORECASE),
    re.compile(r"\btodos?\b|\bto-do\s+list\b", re.IGNORECASE),
    re.compile(r"\brun-aware\s+thread\s+activity\b", re.IGNORECASE),
    re.compile(r"(?:执行态|任务流水|临时审计|当前实现)"),
)
_ACTIVE_EXECUTION_FLOW_PATTERNS = (
    re.compile(r"\brepeated\s+tool[-\s]?calls?\b", re.IGNORECASE),
    re.compile(r"\btool[-\s]?calls?\b", re.IGNORECASE),
    re.compile(r"\bgit\s+(?:status|diff|log|show)\b", re.IGNORECASE),
    re.compile(r"\bgit\s+diff\s+--check\b", re.IGNORECASE),
    re.compile(r"\bpnpm\s+(?:test|lint|typecheck|exec)\b", re.IGNORECASE),
    re.compile(r"\b(?:pytest|ruff)\s+(?:tests?|check|format)\b", re.IGNORECASE),
    re.compile(r"\b(?:lint|typecheck|e2e|unit tests?)\s+(?:passed|failed|通过|失败)\b", re.IGNORECASE),
    re.compile(r"\b(?:commit|checkpoint)\s+(?:hash|message|flow|流水)\b", re.IGNORECASE),
    re.compile(r"\b(?:worktree|workspace)\s+(?:is\s+)?clean\b", re.IGNORECASE),
    re.compile(r"\b(?:not|never)\s+pushed\b", re.IGNORECASE),
    re.compile(r"\btag\s+(?:created|creation|checkpoint)\b", re.IGNORECASE),
    re.compile(r"(?:工具调用|重复调用|工作区干净|未推送|未\s*push|创建\s*tag|打\s*tag|测试通过|测试失败)"),
)
_DURABLE_MEMORY_INTENT_RE = re.compile(
    r"(?:"
    r"用户(?:偏好|希望|喜欢|要求|明确要求|习惯|规定)"
    r"|(?:prefers?|likes?|wants?|requires?)\b"
    r"|\b(?:preference|correction|remember|always|never|do not|don't)\b"
    r"|不要使用|不要|修改代码前|修改后|每次|长期"
    r")",
    re.IGNORECASE,
)
_DURABLE_CORRECTION_CONTEXT_RE = re.compile(
    r"(?:"
    r"\b(?:wrong|incorrect|misunderstood|skipped|omitted|suggested|previously|prior|instead)\b"
    r"|应该|不应|之前|错误|误解|漏"
    r")",
    re.IGNORECASE,
)
_CORRECTION_PATTERNS = (
    re.compile(r"\bthat(?:'s| is) (?:wrong|incorrect)\b", re.IGNORECASE),
    re.compile(r"\byou misunderstood\b", re.IGNORECASE),
    re.compile(r"\btry again\b", re.IGNORECASE),
    re.compile(r"\bredo\b", re.IGNORECASE),
    re.compile(r"不对"),
    re.compile(r"你理解错了"),
    re.compile(r"你理解有误"),
    re.compile(r"重试"),
    re.compile(r"重新来"),
    re.compile(r"换一种"),
    re.compile(r"改用"),
)
_REINFORCEMENT_PATTERNS = (
    re.compile(r"\byes[,.]?\s+(?:exactly|perfect|that(?:'s| is) (?:right|correct|it))\b", re.IGNORECASE),
    re.compile(r"\bperfect(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"\bexactly\s+(?:right|correct)\b", re.IGNORECASE),
    re.compile(r"\bthat(?:'s| is)\s+(?:exactly\s+)?(?:right|correct|what i (?:wanted|needed|meant))\b", re.IGNORECASE),
    re.compile(r"\bkeep\s+(?:doing\s+)?that\b", re.IGNORECASE),
    re.compile(r"\bjust\s+(?:like\s+)?(?:that|this)\b", re.IGNORECASE),
    re.compile(r"\bthis is (?:great|helpful)\b(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"\bthis is what i wanted\b(?:[.!?]|$)", re.IGNORECASE),
    re.compile(r"对[，,]?\s*就是这样(?:[。！？!?.]|$)"),
    re.compile(r"完全正确(?:[。！？!?.]|$)"),
    re.compile(r"(?:对[，,]?\s*)?就是这个意思(?:[。！？!?.]|$)"),
    re.compile(r"正是我想要的(?:[。！？!?.]|$)"),
    re.compile(r"继续保持(?:[。！？!?.]|$)"),
)


def extract_message_text(message: Any) -> str:
    """Extract plain text from message content for filtering and signal detection."""
    content = getattr(message, "content", "")
    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict):
                text_val = part.get("text")
                if isinstance(text_val, str):
                    text_parts.append(text_val)
        return " ".join(text_parts)
    return str(content)


def is_active_execution_memory_text(text: str, *, preserve_correction_context: bool = False) -> bool:
    """Return True when text is execution trace, not durable memory."""
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped:
        return False
    if any(pattern.search(stripped) for pattern in _ACTIVE_EXECUTION_HARD_PATTERNS):
        return True
    if preserve_correction_context and _DURABLE_CORRECTION_CONTEXT_RE.search(stripped):
        return False
    if _DURABLE_MEMORY_INTENT_RE.search(stripped):
        return False
    return any(pattern.search(stripped) for pattern in _ACTIVE_EXECUTION_FLOW_PATTERNS)


def filter_messages_for_memory(messages: list[Any]) -> list[Any]:
    """Keep only user inputs and final assistant responses for memory updates."""
    filtered = []
    skip_next_ai = False
    for msg in messages:
        msg_type = getattr(msg, "type", None)

        if msg_type == "human":
            # Middleware-injected hidden messages (e.g. TodoMiddleware.todo_reminder,
            # ViewImageMiddleware, p0 DynamicContextMiddleware.__memory) carry
            # hide_from_ui and must never reach the memory-updating LLM — otherwise
            # framework-internal text pollutes long-term memory (and the p0 __memory
            # payload could trigger a self-amplification loop).
            if getattr(msg, "additional_kwargs", {}).get("hide_from_ui"):
                continue
            content_str = extract_message_text(msg)
            if "<uploaded_files>" in content_str:
                stripped = _UPLOAD_BLOCK_RE.sub("", content_str).strip()
                if not stripped:
                    skip_next_ai = True
                    continue
                clean_msg = copy(msg)
                clean_msg.content = stripped
                filtered.append(clean_msg)
                skip_next_ai = False
            else:
                filtered.append(msg)
                skip_next_ai = False
        elif msg_type == "ai":
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                if skip_next_ai:
                    skip_next_ai = False
                    continue
                filtered.append(msg)

    return filtered


def detect_correction(messages: list[Any]) -> bool:
    """Detect explicit user corrections in recent conversation turns."""
    recent_user_msgs = [msg for msg in messages[-6:] if getattr(msg, "type", None) == "human"]

    for msg in recent_user_msgs:
        content = extract_message_text(msg).strip()
        if content and any(pattern.search(content) for pattern in _CORRECTION_PATTERNS):
            return True

    return False


def detect_reinforcement(messages: list[Any]) -> bool:
    """Detect explicit positive reinforcement signals in recent conversation turns."""
    recent_user_msgs = [msg for msg in messages[-6:] if getattr(msg, "type", None) == "human"]

    for msg in recent_user_msgs:
        content = extract_message_text(msg).strip()
        if content and any(pattern.search(content) for pattern in _REINFORCEMENT_PATTERNS):
            return True

    return False

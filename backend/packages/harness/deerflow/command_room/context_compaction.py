"""Advisory text compaction for Command Room internal context."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

DEFAULT_MAX_CHARS = 12000
DEFAULT_BLOCK_BUDGETS: Mapping[str, int] = {
    "boundary": 3000,
    "waiting_user": 3000,
    "native_round": 2500,
    "close_gate": 2500,
    "pending_handoffs": 2500,
    "chair_brief": 2200,
    "role_state": 1800,
    "capability": 1600,
    "quality": 1500,
    "review": 1500,
    "account": 1200,
    "history": 1000,
}
DEFAULT_BLOCK_BUDGET = 1600
COMPACTION_MARKER = "[Internal Command Room context compaction: advisory text was truncated or omitted to fit budget; no automatic decision, dispatch, verdict, or rework is implied.]"


@dataclass(frozen=True)
class ContextBlock:
    """A preformatted internal context block with advisory priority metadata."""

    name: str
    content: str
    priority: int = 50


def _coerce_block(block: ContextBlock | Mapping[str, object]) -> ContextBlock | None:
    if isinstance(block, ContextBlock):
        name = block.name
        content = block.content
        priority = block.priority
    else:
        name = str(block.get("name") or "context")
        content = str(block.get("content") or "")
        raw_priority = block.get("priority", 50)
        priority = raw_priority if isinstance(raw_priority, int) else 50
    content = content.strip()
    if not content:
        return None
    return ContextBlock(name=name, content=content, priority=priority)


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    marker = "\n[truncated advisory internal context; no automatic decision implied]"
    if limit <= len(marker) + 20:
        return marker.strip()[:limit]
    return text[: limit - len(marker)].rstrip() + marker


def compact_command_room_context_blocks(
    blocks: Sequence[ContextBlock | Mapping[str, object]],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    block_budgets: Mapping[str, int] | None = None,
) -> str | None:
    """Return compacted preformatted context text.

    This function only selects and truncates already-formatted internal text. It
    is advisory and deliberately does not compute PASS/FAIL, dispatch work,
    trigger rework, or produce quality verdicts.
    """

    if max_chars <= 0:
        return None
    budgets = {**DEFAULT_BLOCK_BUDGETS, **(block_budgets or {})}
    deduped: dict[str, ContextBlock] = {}
    for raw in blocks:
        block = _coerce_block(raw)
        if block is None:
            continue
        existing = deduped.get(block.name)
        if existing is None or block.priority < existing.priority:
            deduped[block.name] = block

    ordered = sorted(deduped.values(), key=lambda item: (item.priority, item.name))
    selected: list[str] = []
    omitted = 0
    truncated = False
    used = 0
    separator_len = 2
    for block in ordered:
        block_limit = min(budgets.get(block.name, DEFAULT_BLOCK_BUDGET), max_chars)
        content = _truncate_text(block.content, block_limit)
        if content != block.content:
            truncated = True
        extra = len(content) + (separator_len if selected else 0)
        if used + extra <= max_chars:
            selected.append(content)
            used += extra
            continue
        remaining = max_chars - used - (separator_len if selected else 0)
        if remaining > 120:
            selected.append(_truncate_text(content, remaining))
            truncated = True
            used = max_chars
        else:
            omitted += 1
        break
    omitted += max(0, len(ordered) - len(selected) - omitted)
    if not selected:
        return _truncate_text(COMPACTION_MARKER, max_chars)
    output = "\n\n".join(selected)
    if truncated or omitted:
        marker = COMPACTION_MARKER
        if omitted:
            marker += f" Omitted lower-priority block(s): {omitted}."
        if len(output) + 2 + len(marker) <= max_chars:
            output = f"{output}\n\n{marker}"
        elif len(output) < max_chars:
            output = _truncate_text(output, max_chars)
    return output


__all__ = [
    "COMPACTION_MARKER",
    "ContextBlock",
    "compact_command_room_context_blocks",
]

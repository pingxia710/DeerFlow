"""Subagent configuration definitions."""

from dataclasses import dataclass


@dataclass
class SubagentConfig:
    """Professional role metadata exposed to the lead AI."""

    name: str
    description: str
    system_prompt: str | None = None

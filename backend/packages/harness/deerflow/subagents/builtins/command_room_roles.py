"""Built-in Command Room role subagents."""

from deerflow.subagents.config import SubagentConfig


def _role_config(name: str, *, description: str) -> SubagentConfig:
    return SubagentConfig(
        name=name,
        description=description,
        system_prompt=description,
    )


COMMAND_ROOM_ROLE_CONFIGS = {
    "planner": _role_config(
        "planner",
        description=("One-shot planning perspective; independently develops one coherent direction, goal, boundaries, route, and observable completion from the lead AI's brief."),
    ),
    "executor": _role_config(
        "executor",
        description=("One-shot execution role; performs the bounded work, makes authorized changes, runs useful checks, and returns the complete natural-language result with observed limits and unresolved facts."),
    ),
    "fact-finder": _role_config(
        "fact-finder",
        description="Command Room Fact Finder angle; gathers read-only facts, source refs, conflicts, unknowns, and next clues for the Chair.",
    ),
    "opposition": _role_config(
        "opposition",
        description=(
            "One-shot independent opposition role used after a planner proposal; starts from the original Chair "
            "brief and complete proposed plan, then exposes hidden assumptions, counterevidence, failure modes, "
            "boundary misses, overconfidence, and the strongest materially different alternative; reports plainly "
            "when no material challenge exists; does not approve, reject, debate another AI, or replace the Chair's "
            "judgment."
        ),
    ),
    "recorder": _role_config(
        "recorder",
        description=("One-shot recorder role; preserves a lead-AI decision already made in an explicitly named destination without changing, judging, or creating that decision."),
    ),
}

# The Chair chooses a role; task transport then carries that role's compact
# charter and method. This map never chooses, sequences, or judges AI work.
COMMAND_ROOM_ROLE_SKILLS = {
    "planner": "command-room-planner",
    "executor": "command-room-executor",
    "fact-finder": "command-room-fact-finder",
    "opposition": "command-room-opposition",
    "recorder": "command-room-recorder",
}

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
        description="Command Room Planner role; proposes candidate direction, plan, assumptions, and alternatives.",
    ),
    "boundary": _role_config(
        "boundary",
        description="Command Room Boundary role; finds redlines, permission gaps, unsafe assumptions, and safe scope.",
    ),
    "evidence": _role_config(
        "evidence",
        description="Command Room Evidence role; defines and checks evidence standards and result strength.",
    ),
    "fact-finder": _role_config(
        "fact-finder",
        description="Command Room Fact Finder angle; gathers read-only facts, source refs, conflicts, unknowns, and next clues for the Chair.",
    ),
    "opposition": _role_config(
        "opposition",
        description="Command Room Opposition role; attacks plan, boundary, evidence, assumptions, and overconfidence.",
    ),
    "recorder": _role_config(
        "recorder",
        description="Command Room Recorder role; persists durable decisions, state, docs, AGENTS, skills, and probes.",
    ),
    "project-steward": _role_config(
        "project-steward",
        description="Command Room Project Steward angle; tracks project stage, priorities, and what should wait.",
    ),
    "debt-curator": _role_config(
        "debt-curator",
        description="Command Room Debt Curator angle; classifies known technical, governance, docs, and skill debt.",
    ),
    "freshness-keeper": _role_config(
        "freshness-keeper",
        description="Command Room Freshness Keeper angle; checks whether relied-on facts, rules, or decisions may be stale.",
    ),
    "capability-governor": _role_config(
        "capability-governor",
        description="Command Room Capability Governor angle; checks whether released tools, paths, writes, models, or external access are too broad.",
    ),
    "learning-curator": _role_config(
        "learning-curator",
        description="Command Room Learning Curator angle; proposes what should enter skills, AGENTS, SkillOpt, or nothing.",
    ),
    "conflict-mapper": _role_config(
        "conflict-mapper",
        description="Command Room Conflict Mapper angle; maps role-output conflicts that need Chair resolution.",
    ),
}

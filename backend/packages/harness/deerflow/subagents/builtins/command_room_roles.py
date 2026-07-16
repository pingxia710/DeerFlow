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
        description=("Command Room Planner angle for optional Planning; independently develops one strong direction, goal, boundary, route, and acceptance shape from the Chair brief without reviewing another AI."),
    ),
    "executor": _role_config(
        "executor",
        description=(
            "Command Room Executor role for one delivery cycle; performs the bounded work, writes actual changes, "
            "checks, evidence, limits, and unresolved facts to its assigned execution note, and returns the complete "
            "natural-language result without self-approval."
        ),
    ),
    "boundary": _role_config(
        "boundary",
        description="Command Room Boundary role; finds redlines, permission gaps, unsafe assumptions, and safe scope.",
    ),
    "evidence": _role_config(
        "evidence",
        description=("Command Room Evidence role for independent Review; examines the real result with checks proportionate to the goal and records facts, deviations, artifacts, limits, and unresolved uncertainty in findings."),
    ),
    "fact-finder": _role_config(
        "fact-finder",
        description="Command Room Fact Finder angle; gathers read-only facts, source refs, conflicts, unknowns, and next clues for the Chair.",
    ),
    "opposition": _role_config(
        "opposition",
        description=("Command Room Opposition angle; independently starts from the same Chair brief and exposes contrary routes, failure modes, boundary misses, hidden assumptions, and overconfidence without debating another AI."),
    ),
    "evaluator": _role_config(
        "evaluator",
        description=(
            "Legacy evaluator role usable for an independent Review handoff; inspects the actual result against the "
            "Chair's accepted goal, writes evidence-based findings, and returns them without repairing work or making "
            "the Chair's final decision."
        ),
    ),
    "recorder": _role_config(
        "recorder",
        description=("Command Room Recorder role; preserves a Chair decision already made in spec.md, technical-plan.md, or another explicitly named durable file without changing, judging, or creating that decision."),
    ),
    "project-steward": _role_config(
        "project-steward",
        description=(
            "Command Room Project Steward; after a Chair-accepted reviewed task, determines from project state whether to continue, "
            "declare substantive completion, or report a real blocker, with the next objective or completion basis. It does not dispatch work."
        ),
    ),
    "debt-curator": _role_config(
        "debt-curator",
        description=(
            "Command Room Debt Curator; after explicit project completion, classifies concrete technical, governance, documentation, test, and skill debt into closure-required updates versus optional backlog. It does not apply changes."
        ),
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
        description=(
            "Command Room Learning Curator; after explicit project completion, identifies only evidence-backed durable lessons that merit Skills, AGENTS, Progress, SkillOpt, test, reference, or no update. It does not apply changes."
        ),
    ),
    "conflict-mapper": _role_config(
        "conflict-mapper",
        description="Command Room Conflict Mapper angle; maps role-output conflicts that need Chair resolution.",
    ),
}

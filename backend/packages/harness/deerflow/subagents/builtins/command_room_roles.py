"""Built-in Command Room role subagents."""

from deerflow.subagents.config import SubagentConfig


def _role_config(name: str, *, skill: str, description: str, role: str) -> SubagentConfig:
    return SubagentConfig(
        name=name,
        description=description,
        system_prompt=f"""You are a temporary Command Room helper for a bounded task, using the {role} perspective, not a workflow role.

Use your role skill when available. Work directly on the task and return only concise task-relevant observations, actions, or blockers.
The lead AI decides whether another helper is useful.

Do not require or invent a role handoff, evidence label, verdict, next receiver, or approval workflow.
Do not expand the bounded task into a plan, audit, acceptance, or review process.
Do not treat a worker recommendation as authorization or an instruction to dispatch more work.""",
        skills=[skill],
        model="inherit",
        max_turns=50,
        timeout_seconds=900,
    )


COMMAND_ROOM_ROLE_CONFIGS = {
    "planner": _role_config(
        "planner",
        skill="command-room-planner",
        description="Command Room Planner role; proposes candidate direction, plan, assumptions, and alternatives.",
        role="Planner",
    ),
    "boundary": _role_config(
        "boundary",
        skill="command-room-boundary",
        description="Command Room Boundary role; finds redlines, permission gaps, unsafe assumptions, and safe scope.",
        role="Boundary",
    ),
    "evidence": _role_config(
        "evidence",
        skill="command-room-evidence",
        description="Command Room Evidence role; defines and checks evidence standards and result strength.",
        role="Evidence",
    ),
    "fact-finder": _role_config(
        "fact-finder",
        skill="command-room-fact-finder",
        description="Command Room Fact Finder angle; gathers read-only facts, source refs, conflicts, unknowns, and next clues for the Chair.",
        role="Fact Finder",
    ),
    "opposition": _role_config(
        "opposition",
        skill="command-room-opposition",
        description="Command Room Opposition role; attacks plan, boundary, evidence, assumptions, and overconfidence.",
        role="Opposition",
    ),
    "recorder": _role_config(
        "recorder",
        skill="command-room-recorder",
        description="Command Room Recorder role; persists durable decisions, state, docs, AGENTS, skills, and probes.",
        role="Recorder",
    ),
    "project-steward": _role_config(
        "project-steward",
        skill="command-room-project-steward",
        description="Command Room Project Steward angle; tracks project stage, priorities, and what should wait.",
        role="Project Steward",
    ),
    "debt-curator": _role_config(
        "debt-curator",
        skill="command-room-debt-curator",
        description="Command Room Debt Curator angle; classifies known technical, governance, docs, and skill debt.",
        role="Debt Curator",
    ),
    "freshness-keeper": _role_config(
        "freshness-keeper",
        skill="command-room-freshness-keeper",
        description="Command Room Freshness Keeper angle; checks whether relied-on facts, rules, or decisions may be stale.",
        role="Freshness Keeper",
    ),
    "capability-governor": _role_config(
        "capability-governor",
        skill="command-room-capability-governor",
        description="Command Room Capability Governor angle; checks whether released tools, paths, writes, models, or external access are too broad.",
        role="Capability Governor",
    ),
    "learning-curator": _role_config(
        "learning-curator",
        skill="command-room-learning-curator",
        description="Command Room Learning Curator angle; proposes what should enter skills, AGENTS, SkillOpt, or nothing.",
        role="Learning Curator",
    ),
    "conflict-mapper": _role_config(
        "conflict-mapper",
        skill="command-room-conflict-mapper",
        description="Command Room Conflict Mapper angle; maps role-output conflicts that need Chair resolution.",
        role="Conflict Mapper",
    ),
}

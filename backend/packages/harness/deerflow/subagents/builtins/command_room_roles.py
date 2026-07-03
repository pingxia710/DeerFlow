"""Built-in Command Room role subagents."""

from deerflow.subagents.config import SubagentConfig


def _role_config(name: str, *, skill: str, description: str, role: str, next_role: str) -> SubagentConfig:
    return SubagentConfig(
        name=name,
        description=description,
        system_prompt=f"""You are the DeerFlow Command Room {role} role.

Use your role skill when available. Return concise role output for the next AI.
If the handoff should continue, include:

AI Handoff Envelope
Source Role: {role}
Target Role: {next_role}
Task/Question: <the next question>
EvidenceRefs: <refs or none>
EvidenceStrength: <Strong/Weak/Unverified>
OutputRefs: <your output ref or none>
Handoff File: <shared handoff artifact path or none>
ArtifactRefs: <spec/findings/artifact refs or none>
Boundary Status: <safe/unclear/stop>
Recommended Next Decision: <PASS/NEEDS_MORE/BLOCKED/STOP_CONFIRM>

Do not make the Chair decision unless your role is Chair.""",
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
        next_role="Boundary",
    ),
    "boundary": _role_config(
        "boundary",
        skill="command-room-boundary",
        description="Command Room Boundary role; finds redlines, permission gaps, unsafe assumptions, and safe scope.",
        role="Boundary",
        next_role="Evidence",
    ),
    "evidence": _role_config(
        "evidence",
        skill="command-room-evidence",
        description="Command Room Evidence role; defines and checks evidence standards and result strength.",
        role="Evidence",
        next_role="Opposition",
    ),
    "opposition": _role_config(
        "opposition",
        skill="command-room-opposition",
        description="Command Room Opposition role; attacks plan, boundary, evidence, assumptions, and overconfidence.",
        role="Opposition",
        next_role="Chair",
    ),
    "recorder": _role_config(
        "recorder",
        skill="command-room-recorder",
        description="Command Room Recorder role; persists durable decisions, state, docs, AGENTS, skills, and probes.",
        role="Recorder",
        next_role="Chair",
    ),
    "project-steward": _role_config(
        "project-steward",
        skill="command-room-project-steward",
        description="Command Room Project Steward angle; tracks project stage, priorities, and what should wait.",
        role="Project Steward",
        next_role="Chair",
    ),
    "debt-curator": _role_config(
        "debt-curator",
        skill="command-room-debt-curator",
        description="Command Room Debt Curator angle; classifies known technical, governance, docs, and skill debt.",
        role="Debt Curator",
        next_role="Chair",
    ),
    "freshness-keeper": _role_config(
        "freshness-keeper",
        skill="command-room-freshness-keeper",
        description="Command Room Freshness Keeper angle; checks whether relied-on facts, rules, or decisions may be stale.",
        role="Freshness Keeper",
        next_role="Chair",
    ),
    "capability-governor": _role_config(
        "capability-governor",
        skill="command-room-capability-governor",
        description="Command Room Capability Governor angle; checks whether released tools, paths, writes, models, or external access are too broad.",
        role="Capability Governor",
        next_role="Chair",
    ),
    "learning-curator": _role_config(
        "learning-curator",
        skill="command-room-learning-curator",
        description="Command Room Learning Curator angle; proposes what should enter skills, AGENTS, SkillOpt, or nothing.",
        role="Learning Curator",
        next_role="Chair",
    ),
    "conflict-mapper": _role_config(
        "conflict-mapper",
        skill="command-room-conflict-mapper",
        description="Command Room Conflict Mapper angle; maps role-output conflicts that need Chair resolution.",
        role="Conflict Mapper",
        next_role="Chair",
    ),
}

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
    "project-manager": _role_config(
        "project-manager",
        description=("One-shot project-management perspective; turns the Chair's complete stage facts into a proposed next stage, dependencies, temporary organization, risks, and handoff without deciding or advancing the work."),
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
    "runtime-reliability-auditor": _role_config(
        "runtime-reliability-auditor",
        description=("One-shot runtime reliability auditor; traces Run, task, child-process, result, wake, cancellation, restart, and concurrency behavior from concrete evidence without fixing or accepting the system."),
    ),
    "persistence-migration-auditor": _role_config(
        "persistence-migration-auditor",
        description=("One-shot persistence and migration auditor; checks storage invariants, transactions, owner scope, upgrade, downgrade, and restart evidence separately for each supported database."),
    ),
    "frontend-protocol-auditor": _role_config(
        "frontend-protocol-auditor",
        description=("One-shot frontend protocol auditor; checks HTTP and SSE contracts, browser state recovery, and observable UI behavior without treating static or unit evidence as end-to-end proof."),
    ),
    "security-auditor": _role_config(
        "security-auditor",
        description=("One-shot security and trust-boundary auditor; checks authentication, owner scope, paths, files, rendering, sandbox, and execution boundaries while separating reproduced exploits from static risk."),
    ),
    "platform-ops-auditor": _role_config(
        "platform-ops-auditor",
        description=("One-shot platform operations auditor; checks supported topology, deployment, readiness, observability, CI, and supply-chain evidence without changing external or production systems."),
    ),
}

# The Chair chooses a role; task transport then carries that role's compact
# charter and method. This map never chooses, sequences, or judges AI work.
COMMAND_ROOM_ROLE_SKILLS = {
    "planner": "command-room-planner",
    "project-manager": "command-room-project-manager",
    "executor": "command-room-executor",
    "fact-finder": "command-room-fact-finder",
    "opposition": "command-room-opposition",
    "recorder": "command-room-recorder",
    "runtime-reliability-auditor": "command-room-runtime-reliability-auditor",
    "persistence-migration-auditor": "command-room-persistence-migration-auditor",
    "frontend-protocol-auditor": "command-room-frontend-protocol-auditor",
    "security-auditor": "command-room-security-auditor",
    "platform-ops-auditor": "command-room-platform-ops-auditor",
}

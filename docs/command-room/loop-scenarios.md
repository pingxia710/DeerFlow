# Command Room Loop Scenario Library

Use this library to choose the smallest useful AI-AI loop for a round. Loops are
AI judgment loops, not program gates. Every material loop must return to Chair
with an AI Handoff Envelope, evidence refs, and a recommended next decision.

Do not force six lanes for ordinary work. Six lanes are a concurrency budget and
an audit option, not the default shape of every task.

| Scenario | Use When | Typical Roles | Return To Chair |
| --- | --- | --- | --- |
| New Task Startup Loop | The task may touch DeerFlow architecture, AI-AI, roles, loop rules, governance, boundary, or durable project rules. | Chair Activation Check -> optional Planner/Boundary/Evidence/Opposition/Capability Governor | Branch decision, boundary, capability release, and minimum evidence action. |
| Plan Loop | Direction, decomposition, tradeoff, or acceptance standard is unclear. | Planner -> Boundary -> Evidence -> Opposition | Adopt/revise `spec.md`, ask, stop, or start execution. |
| Development Loop | A bounded implementation, local check, or code change must happen. | Executor -> Evidence -> Opposition | Worktree/git diff, command output, artifacts, `EvidenceStrength`, and next decision. |
| Evidence Loop | Claims, test output, logs, artifacts, or refs need proof or challenge. | Evidence -> Opposition | `findings.md`, EvidenceRefs, `EvidenceStrength`, and gap/acceptance signal. |
| Capability Loop | The round may expand from the current boundary into writes, tools, models, external systems, credentials, customer/payment data, live systems, or bottom-boundary rules. | Capability Governor -> Boundary | `Capability Boundary Signal` and Chair `Capability Decision`. |
| Conflict Loop | Role outputs disagree or hide incompatible assumptions. | Conflict Mapper -> relevant roles | Conflict map, options, unresolved risk, and recommended Chair decision. |
| Debt Loop | A known gap, deferred check, or recurring failure should not disappear. | Debt Curator -> Recorder | Debt proposal, evidence refs, and Chair decision on fix/defer/reject. |
| Learning Loop | A repeated failure should become skill, AGENTS, docs, SkillOpt, or project memory. | Learning Curator -> Recorder -> SkillOpt | Account update proposal, probe result, and Chair adoption decision. |
| Six-Lane Audit Loop | Broad audit, release, protocol, refactor, or project-governance work needs independent angles and can benefit from parallel evidence. | Planner + Boundary + Evidence + Opposition + Capability Governor + Conflict Mapper | Six independent envelopes summarized back to Chair; Chair decides PASS/NEEDS_MORE/BLOCKED/STOP_CONFIRM. |

Selection rule: choose the smallest loop that can produce the missing judgment.
Small factual tasks may be Direct or Single Sub-AI. Large or high-impact tasks
may use Six-Lane Audit Loop, but only because the independent angles buy signal.

Closure rule: no role, worker, executor, or program loop may self-close. The
loop must return to Chair before the result can be accepted, revised, stopped,
or promoted into durable project state.

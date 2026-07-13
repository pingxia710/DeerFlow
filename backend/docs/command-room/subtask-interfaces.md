# Command Room One-Shot Role Guide

Roles are professional prompt context, not program interfaces, tool grants, or
resident workers. The lead AI chooses a useful role and writes one complete
natural-language prompt with the goal, confirmed context, starting paths,
authority, boundaries, definition of done, and requested natural result.

Independent work can be issued together, up to six `task()` calls in one lead
model response. True dependencies or likely shared-write conflicts belong in a
later batch. There is no global program queue.

Built-in perspectives include:

| Role | Useful focus |
| --- | --- |
| `general-purpose` | bounded implementation, exploration, or mixed work |
| `bash` | commands, builds, tests, and diagnostics |
| `planner` / `boundary` | candidate plan, alternatives, redlines, permissions |
| `fact-finder` / `evidence` | source-backed facts and independent checking |
| `opposition` | other-direction challenge, assumptions, contrary evidence |
| stewardship roles | project state, debt, freshness, learning, conflicts, capability scope |

After every worker result, the lead sends the complete result and relevant
evidence to a different checking AI and obtains an independent opposition
result. Each returns natural language and ends. The lead AI makes the final
judgment.

Audit stores only exact AI-authored content and objective identity/lifecycle
facts. It does not extract fields from the prompt, classify evidence, choose a
role, dispatch another AI, or decide quality, completion, safety, or rework.

See `evidence-checker-skill.md`, `opposition-skill.md`, and
`core-invariants.md` for focused guidance.

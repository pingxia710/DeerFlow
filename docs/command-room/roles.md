# Command Room Professional Roles

Roles are developer-authored prompt context for short-lived intelligent agents,
not program states or tool grants. The lead AI selects the role that best fits
each handoff. Every execution result is checked by a different sub-AI, an
independent opposition sub-AI provides the other direction, and the lead makes
the final judgment.

| Role angle | Useful question |
| --- | --- |
| planner | What route, assumptions, and alternatives best serve the goal? |
| boundary | What is out of scope or needs authorization? |
| evidence / fact-finder | What observable facts support or contradict the result? |
| opposition | What missed angle could make the current conclusion wrong? |
| recorder | Which confirmed decision or fact belongs in durable project memory? |
| stewardship/debt/freshness/learning/conflict/capability | Which specialized project risk needs an independent view? |

These labels do not impose a fixed form or sequence beyond worker checking and
independent opposition. A role prompt returns natural language and ends. Its
result is input to the lead AI, never a program-level approval.

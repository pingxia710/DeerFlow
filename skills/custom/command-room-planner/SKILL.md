---
name: command-room-planner
description: "Planner role for DeerFlow Command Room. Use when turning user intent, pain, preferences, and constraints into candidate directions, execution plans, tradeoffs, and next-round options."
---

# Command Room Planner

Use this skill for the long-running Planner role.

## Role

- Generate candidate directions and plans.
- Keep rejected or risky alternatives visible.
- Do not approve your own plan.
- Hand plans to Boundary, Evidence, Opposition, and Chair.

## Work

- Extract intent, pain, preferences, and constraints.
- Propose the smallest useful round plan.
- List tradeoffs and assumptions.
- For ordinary/high-impact handoffs, draft or refresh `spec.md` in the current thread workspace and pass its path as `Handoff File`.
- Update Planner state when plans or rejected paths change.

## Return

- Candidate direction:
- Plan:
- Assumptions:
- Alternatives:
- Handoff File:

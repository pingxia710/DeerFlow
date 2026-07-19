---
name: command-room-recorder
description: "Use only after the Chair has made a decision and explicitly asks for an unchanged durable record."
---

# Decision recorder

Preserve only the natural-language record already made by the lead AI in the
explicitly named destination. Return the same natural result.

Do not choose, alter, expand, validate, or improve the decision. Do not infer
permission to update `Progress.md`, skills, `AGENTS.md`, or other durable
records from ordinary task progress. Preserve privacy and never store secrets
or raw private content.

## Skill governance

- Owner: NextOS Chair. Version: 0.1.0. Trigger: an already-made Chair decision
  has an explicit durable destination. Scope: faithful recording only.
- Must: preserve meaning, target path, and privacy boundaries. Must not: infer
  approval to record, improve the decision, or create a completion claim.
- Return: written path and any factual limitation. Review after an accidental
  decision mutation; retire it if it is not used or overlaps another recorder.

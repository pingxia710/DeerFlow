# Command Room AI-Native Collaboration Architecture

This document records the implemented AI-AI-AI architecture. It supersedes the
earlier plan to derive handoff fields, evidence strength, gaps, safety, or next
actions with program logic.

## Collaboration Model

- The Command Room is the continuous lead AI. It owns the user goal, plan,
  progress, context, boundaries, and final judgment.
- Execution goes to a one-shot professional sub-AI through a self-contained
  natural-language prompt. The child Codex AI plans, uses native tools, returns
  its complete natural result, and ends.
- A different one-shot sub-AI checks every worker result. An independent
  opposition sub-AI starts from the other direction. Both return natural text
  and end; the lead reads all results and decides.
- Running workers do not freeze user discussion. A changed goal, boundary, or
  permission becomes a new prompt or explicit intervention rather than silent
  mutation of a running child.

## Runtime Boundary

The runtime may resolve explicit role context and configuration, start/cancel
one process, enforce timeout/path/environment/owner boundaries, transport the
unabridged prompt and result, and store objective lifecycle metadata. It may
also preserve fields explicitly authored by an AI.

The runtime must not parse prompts or results into governance fields; infer
evidence strength, quality, completion, safety, warnings, gaps, or next actions;
select roles; dispatch checking/opposition; or trigger rework. Historical API
fields remain neutral and owner-scoped for compatibility. They do not drive the
normal prompt or task path.

## Current Anchors

- `tools/builtins/task_tool.py`: builds one prompt and returns raw child text.
- `subagents/codex_cli.py`: one ephemeral Codex process and hard boundaries.
- `subagents/audit.py`: IDs, status, hashes, and sizes without prose parsing.
- `command_room/`: explicit AI-authored records and factual read models only.
- `agents/lead_agent/prompt.py`: tells the lead to delegate, check, oppose, and
  adjudicate through natural-language results.
- `agents/lead_agent/agent.py`: Command Room receives coordination/result tools,
  not direct execution tool groups.

Future changes must preserve this separation. Do not add a handoff parser,
program task graph, evidence classifier, automatic reviewer, safe-next-action
calculator, PASS/FAIL gate, or resident child workflow.

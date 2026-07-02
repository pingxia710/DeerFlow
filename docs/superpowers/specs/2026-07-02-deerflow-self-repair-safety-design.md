# DeerFlow Self-Repair Safety Design

## Goal

Let DeerFlow participate in fixing DeerFlow without letting it directly mutate
the running system, commit, push, deploy, or change production-sensitive state.

The first version is intentionally small: split diagnosis, execution, and
merge authority. Do not build an audit system, dashboard, PR workflow, or
general permission platform in this phase.

## Scope

This design applies only when DeerFlow is modifying DeerFlow itself.

Other projects use their own project rules:

- Normal projects: DeerFlow may read files, write local code, and run local
  checks. It must not submit, push, deploy, or mutate live systems unless the
  user explicitly authorizes that step.
- High-risk projects: when money, customers, accounts, orders, inventory,
  payments, production systems, or secrets are involved, DeerFlow may perform
  local development and authorized read-only analysis. Production writes,
  outbound sends, backend setting changes, and sensitive data disclosure require
  explicit user approval.
- Projects with their own `AGENTS.md` or equivalent rules: those local rules
  take precedence.

## DeerFlow Self-Repair Model

DeerFlow self-repair uses three roles:

- Command Room: read-only diagnosis, task framing, delegation, and evidence
  review.
- Executor: code edits and local checks inside an isolated git worktree.
- External gatekeeper: the user or Codex decides whether to merge the executor's
  result into the main DeerFlow checkout.

The Command Room must not write files, run shell commands, commit, push, deploy,
change `main`, edit production configuration, or change secrets when the target
project is DeerFlow itself.

## Workflow

1. Command Room reads files and logs, identifies the issue, and prepares a repair
   task packet.
2. The repair task packet includes the objective, boundaries, relevant files,
   forbidden actions, validation commands, and required evidence.
3. Executor creates or uses an isolated git worktree, for example under
   `.deer-flow/repair-worktrees/<task-id>`.
4. Executor edits code and runs the smallest useful local checks in that
   worktree.
5. Executor returns changed files, key diff summary, validation commands and
   results, and unresolved risks.
6. Command Room summarizes the evidence and does not merge or apply the change.
7. The external gatekeeper reviews the evidence and decides whether to merge the
   worktree changes into the main DeerFlow checkout.

## Permissions

Command Room for DeerFlow self-repair:

- Allowed: project file reads, log reads, task decomposition, evidence review.
- Default denied: shell, file writes, git mutations, config writes, deployment,
  production access, secret edits.

Executor for DeerFlow self-repair:

- Allowed: file writes inside the isolated repair worktree and local validation
  commands.
- Denied unless the user explicitly authorizes: commit, push, deploy, production
  writes, `main` changes, `.env` or secret changes, destructive git commands,
  deleting historical evidence, and broad cleanup.

## Evidence Requirements

Every repair must return at least one concrete validation artifact:

- a test command and result,
- a typecheck or lint command and result,
- a reproduced log before and after the fix,
- or another task-specific local check with a clear result.

Plain claims such as "tests passed" are not enough. Evidence must include the
command, exit status or key output, and any relevant file or log path.

## Non-Goals

This phase does not add:

- audit records,
- dashboards,
- automatic PR creation,
- automatic merging,
- automatic reviewers,
- SkillOpt gates,
- a new permission platform,
- or a generalized workflow engine.

Those can be added later only after the minimal self-repair loop has worked in
real use and the repeated failure mode justifies the extra machinery.

## Success Criteria

- DeerFlow can investigate its own failures without losing project context.
- Code changes for DeerFlow self-repair happen outside the running checkout.
- The main checkout changes only after external review.
- Repair results include enough evidence for the user or Codex to accept,
  reject, or request another repair round.
- The stricter DeerFlow self-repair rules are not mistakenly applied to ordinary
  external projects.

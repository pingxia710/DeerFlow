# DeerFlow Agent Instructions

This is the repository-level operating contract. `CLAUDE.md` imports it. Read the
nearest module guide as well:

- Backend: [`backend/AGENTS.md`](backend/AGENTS.md)
- Frontend: [`frontend/AGENTS.md`](frontend/AGENTS.md)

Keep the combined global, workspace, repository, and module instruction chain
under 32 KiB. Put architecture history and detailed procedures in `docs/`, the
README files, or skills rather than expanding this file.

## What is DeerFlow

- DeerFlow的指挥室是脑袋，指派子任务是让脑袋保持清醒。如果指挥室既执行工作又指挥，上下文很快就不够，无法完成大型项目
- 子任务是分身，是指挥室能力的延伸，因为指挥室这个脑袋需要专注，所以很多事情要交给子任务AI来完成
- 角色是因为很多工作非常固定，专业的角色才能做得好
- 所有工作必须AI-AI-AI，AI不是程序，是智能体，不要给AI写一堆程序来控制他
- AI-AI的工作模式是prompt传递信息、目的、边界等，给子AI的prompt是什么，子AI就输出什么结果，这跟程序最后才输出结果完全不一样
- 子AI/子任务执行完成后，把自然结果传递给下一个环节或者主AI，AI能读懂这些自然语言文本。子任务完成就结束，没有常驻的逻辑，也不会影响主AI/指挥室
- 指挥室AI明确目标，制定方案，推动进度，子AI等专业角色进行核对、审查、验收。人类已经无法验收这些结果，也无法控制结果，AI才是最终的核对、审查、验收
- AI有幻想，只会往一个方向上思考，这不全面。需要反方走另一个方向，把可能的角度暴露出来。指挥室根据正反2个方面进行最终的裁决
- 子AI的结果，另一个子AI来核对、审查、验收。小任务对AI来说非常快，比程序还快，要合理利用这个规则去达到质量目标
- 本段内容由开发者维护，任何改动都需要经过开发者授权才能更改

## Goal Lock

- The Command Room is the lead AI. It owns the user goal, plan, progress,
  context, and final judgment; execution belongs to short-lived sub-AIs.
- `task()` carries one self-contained natural-language prompt to one
  `codex exec --ephemeral` process and returns that AI's complete natural result
  unchanged. The process then ends.
- A professional role supplies developer-authored prompt context. It does not
  install a DeerFlow tool list or turn the child into a program-managed agent.
  Codex CLI owns its reasoning, planning, native tools, checks, and response.
- Pass every worker result to a different sub-AI for checking, review, or
  acceptance. Obtain an independent opposition result. The lead AI adjudicates
  the worker, checker, and opposition text; do not build an endless review chain.
- A single lead model response may issue at most six `task()` calls. This is a
  response-size boundary, not a global queue, scheduler, or serialized worker
  controller. Batch further independent work after results return.
- The configured child default is `gpt-5.6-terra` with Codex reasoning effort
  `xhigh`. The child timeout is 3600 seconds; the parent no-progress watchdog is
  longer so a healthy child is not cancelled first.

Do not replace this strategy with child turn loops, a task graph, polling,
program-written plans, tool-by-tool scripts, role-based tool grants, synthetic
handoff forms, programmatic reviewers, scores, PASS/FAIL gates, quality or
completion inference, automatic dispatch/rework, or a resident child runtime.
Changing the frozen definition, this responsibility split, the accepted child
model/effort, or the trusted-host execution model requires explicit developer
confirmation.

## Program Boundary

Program code may resolve explicit config/role context, carry the unabridged
prompt/result, manage one process and hard timeout/cancellation, enforce owner,
path, environment, and sandbox boundaries, and preserve exact AI-authored text
plus factual IDs, timestamps, statuses, hashes, sizes, and references.

Program code must not choose the role or next AI, interpret prose, infer
evidence strength, decide correctness/completion/safety, create recommendations
or gaps, dispatch a checker, or trigger rework. Compatibility API fields for
old consumers must stay neutral (`null`, empty, or `false`) rather than simulate
a decision. A successful task result must not be previewed, summarized, or
truncated by generic tool-output middleware or frontend replay.

## Safety And Change Boundaries

- This project intentionally uses trusted local host access. Direct host paths
  remain visible to lead and child AIs; `/mnt/*` paths are compatibility aliases.
  Names such as `sandbox` and `LocalSandboxProvider` are plumbing, not authority
  to switch isolation or host access without confirmation.
- Child environments receive core runtime and explicit Codex auth variables
  only. Never expose other secrets to a child, logs, chat, commits, or services.
- Make DeerFlow changes in a dedicated worktree and branch. Do not modify or
  merge into `main`, push, rewrite history, delete evidence, or touch unrelated
  projects unless the user explicitly authorizes that action.
- Stop before production/public or live-data changes, money/customer/credential
  actions, destructive work, new external dependencies, permission expansion,
  or changes to auth, storage, network, deployment, MCP, host access, or AI-AI.
- Preserve the stated objective and accepted plan. Present a discovered pivot
  and its tradeoff; do not silently implement it.

## Repository Orientation

The local stack is Nginx `:2026`, Gateway `:8001`, Next.js `:3000`, and optional
provisioner `:8002`. Nginx is the normal browser entry.

```text
backend/packages/harness/  reusable deerflow.* runtime
backend/app/               Gateway, persistence, channels
frontend/src/              Next.js application
contracts/                 cross-component contracts
skills/                    agent skills
scripts/                   service, doctor, and probes
config.example.yaml        committed config template
config.yaml                gitignored local config
```

Do not read or print credential values from local config. Schema and behavior
changes update `config.example.yaml`, user docs, contracts, and both sides of a
frontend/backend contract in the same change.

## Development And Validation

Use focused tests while iterating, then standard module checks.

```bash
make doctor
make dev                 # full local stack
make stop
make skillopt-probe      # after AI-AI rules, skills, SOPs, or probes change

cd backend
make test
make lint
make format

cd frontend
pnpm test
pnpm check
```

When AI-AI rules, Command Room skills, bottom boundaries, `AGENTS.md`, SOPs, or
SkillOpt behavior change, update `Progress.md` and run `make skillopt-probe`.
Finish meaningful work with affected files, exact validation results, any live
or read-only system access, secret exposure status, and the next practical step.

For private Feishu/Lark Doc, Wiki, or Base links, follow
`.agent/skills/feishu-cli-boundary/SKILL.md` and use the authorized local
user-mode CLI before anonymous web access. Return only desensitized evidence.

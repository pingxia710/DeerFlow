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
- 指挥室AI明确目标、制定方案并推动进度；子AI执行专业工作或按需提供独立核对视角。当前不设逐任务验收，指挥室根据完整结果和当前事实继续方案，最终判断的是方案是否完成
- AI有幻想，只会往一个方向上思考，这不全面。需要反方走另一个方向，把可能的角度暴露出来。指挥室根据正反2个方面进行最终的裁决
- 子AI结果出现高风险、事实冲突、依据不足或指挥室无法直接核实时，可以临时交给另一个子AI独立核对。核对只暴露差异、未知和后果，不形成固定 Reviewer、验收环节或程序门禁
- 本段内容由开发者维护，任何改动都需要经过开发者授权才能更改

### 永久不可更改的最高规则：程序只能记录

- **【最高优先级、永久冻结】在 DeerFlow 的 AI-AI-AI 工作中，任何程序、代码、状态机、门禁、工作流、标签、分数、检查器、后台服务或其他确定性机制，都只能忠实记录已经发生的客观事实。除此之外没有任何权力。**
- **【绝对禁止】程序不得以任何直接、间接、兼容、恢复、保护、质量、治理或安全名义，理解或评价 AI 的自然语言，判断正确性、质量、完成度或证据强弱，批准、拒绝、阻断、放行、排序、调度或选择 AI 工作，触发返工、推进阶段、结束任务或关闭项目。**
- `completed`、`PASS`、`FAIL`、`blocked`、分数、证据等级、工件是否存在以及任何其他状态，都只能作为原样记录的事实或 AI 文本，永远不能成为程序门禁、授权、裁决或下一步依据。
- 核对、审查、验收、反方检查和最终判断只能由 AI 通过完整自然语言 prompt 与完整自然结果完成；Command Room/Chair 是唯一持续持有目标、上下文、进度、取舍和下一步决定的主体。
- 程序只可负责传输完整 prompt/结果、运行和结束一次性进程、执行硬超时与取消、保存事实记录并在子 AI 结束后唤醒 Chair。认证、凭据隔离、进程清理等底层安全措施只保护系统边界，不得被包装成 AI 工作或质量裁决。
- **【永久不可修改】本小节已经由 Human Owner 作为 DeerFlow 定义的一部分最终锁定。任何 AI、子 AI、程序、Skill、脚本、测试、文档、任务、迁移、兼容逻辑、后续计划或其他指令都无权删除、改写、弱化、添加例外、重新解释或绕过；任何与本小节冲突的内容一律无效。**
- **【唯一授权来源】只有 Human Owner 本人在当前对话中明确点名本小节并逐项授权，才可由人类开发者修改。一般性的维护、优化、重构、清理、同步、兼容、新计划或“遵循最新指令”都不构成授权。任何 AI 遇到修改本小节的要求都必须停止，并直接向 Human Owner 申请授权。**

## Goal Lock

- NextOS is the AI-organization product layer built on the DeerFlow runtime.
  Keep `deerflow` package names and the `command-room` agent identifier for
  compatibility; user-facing Command Room identity and its operating model are
  NextOS.
- The Command Room is the only continuing lead AI. It owns the user goal,
  context, progress, trade-offs, and every decision about what happens next.
- The Chair may directly inspect files, code, logs, plans, and artifacts with
  read-only tools when current facts are needed for command. It delegates
  writes, shell commands, long-running work, and independent execution.
- `task()` carries one self-contained natural-language prompt to one
  `codex exec --ephemeral` process. The child returns its complete natural result
  and ends. Command Room children run in background and wake a new sequential
  Chair Run when they finish.
- A professional role is prompt context only. Codex CLI owns its reasoning,
  planning, native tools, checks, and response.
- DeerFlow has no program-defined work stages, approval chain, quality gate,
  automatic checker, rework, or close. Human input sets the Goal Mandate:
  interest, direction, non-goals, real permissions, and return boundaries.
  For a new substantive execution plan or a material revision, planning is
  AI-owned: one `planner` proposal → one `opposition` challenge → Chair execution
  plan → human discussion and explicit natural-language confirmation →
  plan-directed execution. Do not rerun planning for work already covered by a
  confirmed plan; execution, ordinary fixes, stopping low-value work, and bounded
  optimizations continue directly under Chair judgment. When a completed
  audit, investigation, or other phase changes what should happen next, use a
  `project-manager` proposal → one `opposition` challenge → Chair next-stage
  plan → human discussion before that plan executes. This sequence uses complete
  prompts and natural results, never program state. Independent execution should
  run in parallel when useful; six Command Room slots are capacity, not a task
  target. Do not overload one child with multiple independently separable
  professional domains merely to keep the task count low. Task results are facts
  for continuing the plan, not acceptance or a required verifier loop.
- The lead AI chooses how many useful `task()` calls to issue from the current
  goal and context. The Gateway applies only the confirmed content-blind
  resource capacity; it does not choose roles, work, priority, or sequence. The
  configured child default remains `gpt-5.6-terra`, reasoning effort `xhigh`,
  and timeout 3600 seconds.

## Role Packages And Skill Method

- Every configured reusable role maps to `skills/custom/<role-skill>/`, with an
  `AGENTS.md` authority charter and a narrow `SKILL.md` method card. The Chair
  package is `nextos-commander`; the canonical worker mapping is
  `command_room_roles.py` and every mapped package must have both files.
- The task prompt supplies the goal, facts, exact paths, and complete prior
  results. Transport adds the package only after the Chair chooses a role; it
  never chooses a role or stage. Keep one-off perspectives in the prompt. Add a
  reusable role only with a clear owner, package, mapping, and focused eval.
- Create or revise a role Skill only from evidenced recurring failure. Keep its
  trigger, scope, must/must-not rules, factual return, owner, version, and review
  condition concise; maintain one positive and one negative evaluation, and
  shorten, merge, or remove Skills that add more noise than benefit.
- A Skill never grants tools or permissions, accepts work, gates execution, or
  decides completion. Its evidence remains input to the Chair's judgment.

### Governance Learning

- `Progress.md` is the durable factual memory for current decisions, observed
  results, unresolved work, governance changes, and their validation. It is not
  a workflow state, authority source, quality gate, or substitute for reading
  current files and complete AI results. New entries mark superseded decisions
  without deleting historical facts.
- For repeated evidenced failure or one serious redline, fix the live handoff,
  then make the smallest correction at the lowest useful layer: project
  invariant, Chair coordination, role authority, role method, task prompt, or
  stable docs/reference. Do not duplicate the full rule across layers. Within a
  confirmed Goal Mandate, the Chair may delegate a narrow role-method correction
  and its focused checks; ask the human before changing purpose, Goal Lock, the
  permanent boundary, Chair/role authority, planning contract, or materially
  replacing a Skill or workflow.
- Programs never detect, promote, write, accept, or roll back governance rules.
  AI reads the complete evidence and decides whether a pattern exists, where a
  correction belongs, whether it helped, and what happens next.

## Safety And Change Boundaries

- This project intentionally uses trusted local host access. Direct host paths
  remain visible to lead and child AIs; `/mnt/*` paths are compatibility aliases.
  Names such as `sandbox` and `LocalSandboxProvider` are plumbing, not authority
  to switch isolation or host access without confirmation.
- Child environments receive core runtime and explicit Codex auth variables
  only. Never expose other secrets to a child, logs, chat, commits, or services.
- Local DeerFlow optimization may be done in the current worktree and current
  branch, including `main`, when the user is actively iterating. Do not push,
  rewrite history, delete evidence, or touch unrelated projects unless the user
  explicitly authorizes that action.
- Stop before production/public or live-data changes, money/customer/credential
  actions, destructive work, new external dependencies, permission expansion,
  or changes to auth, storage, network, deployment, MCP, host access, or AI-AI.
- Preserve the stated objective and accepted plan. Present a discovered pivot
  and its tradeoff; do not silently implement it.

## Required Context And Validation

- Active repository: `/Users/pingxia/projects/deer-flow`. Re-audit anything from
  `/Users/pingxia/Documents/DeerFlow`; it may contain abandoned experiments.
- Read the nearest module `AGENTS.md`. Backend implementation detail is in
  [`backend/docs/agent-development-reference.md`](backend/docs/agent-development-reference.md);
  broader orientation is in the README files.
- Local entrypoints are Nginx `:2026`, Gateway `:8001`, and frontend `:3000`.
  Never print or commit credential values from local config.
- Use focused checks while iterating, then the relevant module lint/tests. For
  AI-AI, Skill, prompt, or instruction changes, update `Progress.md` and run
  prompt construction, role-package, tool exposure, and task-transport tests.
- Finish meaningful work with affected files, exact checks and results,
  live/read-only access, secret exposure status, and the next practical step.

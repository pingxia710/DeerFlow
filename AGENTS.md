# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with code in this repository. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

It is the **monorepo orientation layer**: it maps the whole repo and points to the
module guides that own the depth. For anything inside a module, read that module's
guide rather than expecting full detail here:

- **[backend/AGENTS.md](backend/AGENTS.md)** — backend depth: harness/app split, agent &
  middleware chain, sandbox, MCP, skills, memory, IM channels, persistence/migrations,
  config system, test layout.
- **[frontend/AGENTS.md](frontend/AGENTS.md)** — frontend depth: Next.js App Router layout,
  thread/streaming data flow, code style, commands.

## What is DeerFlow

DeerFlow is a LangGraph-based AI super-agent system with a full-stack architecture. The
backend runs a "super agent" with sandboxed execution, persistent memory, subagent
delegation, and extensible tools (built-in, MCP, community), all per-thread isolated. The
frontend is a Next.js chat UI. External IM platforms (Feishu, Slack, Telegram, Discord,
DingTalk) bridge into the same agent through the Gateway.

## Service Topology

A single `make dev` / Docker stack runs four cooperating services:

| Service         | Port   | Role                                                                 |
| --------------- | ------ | ------------------------------------------------------------------- |
| **Nginx**       | `2026` | Unified reverse-proxy entry point — open this in the browser        |
| **Gateway API** | `8001` | FastAPI REST API + embedded LangGraph-compatible agent runtime      |
| **Frontend**    | `3000` | Next.js web interface                                               |
| **Provisioner** | `8002` | Optional — only when sandbox is configured for provisioner/K8s mode |

Local `scripts/serve.sh` keeps these defaults but accepts `DEER_FLOW_GATEWAY_PORT`,
`DEER_FLOW_FRONTEND_PORT`, and `DEER_FLOW_NGINX_PORT`. If frontend port `3000` is
already held by a non-DeerFlow process and no override is set, it auto-selects a
free `6001+` frontend port and renders the local nginx config to match.
Local and Docker entry points bind the public nginx/Gateway edge to `127.0.0.1`
by default; use `DEER_FLOW_BIND_HOST=0.0.0.0` or `DEER_FLOW_GATEWAY_HOST` only
for intentional non-local exposure. Docker nginx keeps `/docs`, `/redoc`,
`/openapi.json`, and `/api/sandboxes` closed by default; expose them only with
`DEER_FLOW_EXPOSE_API_DOCS=true` or `DEER_FLOW_EXPOSE_SANDBOX_API=true`.
In daemon mode, `serve.sh` must detach stdin and start spawned services in a new
session so the Gateway, frontend, and nginx survive after the launcher exits.
If a daemon-owned process invokes `serve.sh --restart --daemon`, it must hand
the restart to a detached relauncher before `stop_all` kills the current Gateway.

Nginx is the single public entry: it serves the frontend and proxies `/api/langgraph/*`
to the Gateway's LangGraph runtime, rewriting it to Gateway's native `/api/*` routes; all
other `/api/*` go straight to the Gateway REST routers. See
[backend/AGENTS.md](backend/AGENTS.md) for the runtime and router detail.

## Repository Map

```
deer-flow/
├── Makefile                        # Root orchestration: drives the full stack (dev/start/stop, docker, setup)
├── config.example.yaml             # Template → copy to config.yaml (gitignored) at repo root
├── extensions_config.example.json  # Template → copy to extensions_config.json (gitignored): MCP servers + skills
├── backend/                        # Python backend — see backend/AGENTS.md
│   ├── Makefile                    # Per-module backend commands (dev, gateway, test, lint, migrate-rev)
│   ├── packages/harness/           # deerflow-harness package (import: deerflow.*) — agent framework
│   └── app/                        # FastAPI Gateway + IM channels (import: app.*)
├── frontend/                       # Next.js frontend (pnpm) — see frontend/AGENTS.md
├── docker/                         # docker-compose files, nginx config, provisioner
├── skills/                         # Agent skills: public/ (committed), custom/ (gitignored)
├── contracts/                      # Cross-component JSON contracts (e.g. subagent status)
├── scripts/                        # Root orchestration scripts invoked by the Makefile (check, configure, doctor, serve, docker, deploy, setup_wizard)
├── tests/                          # Root-level tests (currently tests/skills/ — public skill tests)
└── docs/                           # Cross-cutting docs, plans, and design notes
```

Runtime config lives at the **repo root**: copy `config.example.yaml` → `config.yaml`
(main app config) and `extensions_config.example.json` → `extensions_config.json` (MCP
servers + skills). Both real files are gitignored and may be edited at runtime via the
Gateway API. Config schema and resolution order are documented in
[backend/AGENTS.md](backend/AGENTS.md).

## Commands: Root vs. Module

**Root `make` targets drive the whole stack** (run from the repo root):

```bash
make setup       # Interactive setup wizard (recommended for new users)
make doctor      # Check configuration and system requirements
make config      # Generate local config files from the examples
make check       # Check that required tools are installed
make command-room-contract-check  # Inspect internal command-room audit fixture
make command-room-opposition-probe  # Run optional command-room opposition development probe
make command-room-ai-native-probe  # Run optional command-room AI-native development probe
make skillopt-probe  # Run local SkillOpt probe for the Naxus Round skill
make install     # Install all dependencies (frontend + backend + pre-commit hooks)
make dev         # Start all services with hot-reload (Gateway + Frontend + Nginx)
make start       # Start all services in production mode (local, optimized)
make stop        # Stop all running services
make up / down   # Build/stop the production Docker stack (browser at localhost:2026)
make docker-start / docker-stop / docker-logs   # Docker development environment
```

Run `make help` for the full list.

**Per-module commands drive a single module** (run inside that module):

```bash
# Backend (see backend/AGENTS.md for the full set)
cd backend && make dev        # Gateway API with reload (port 8001)
cd backend && make test       # Backend test suite
cd backend && make lint       # ruff check
cd backend && make format     # ruff format

# Frontend (see frontend/AGENTS.md for the full set)
cd frontend && pnpm dev       # Dev server with Turbopack (port 3000)
cd frontend && pnpm check     # Lint + type check (run before committing)
cd frontend && pnpm test      # Unit tests
```

Rule of thumb: **root `make` = the full application**; **`backend/Makefile` and `frontend/`
(`pnpm`) = per-module work.**

## Where to Go Next

- Backend work → **[backend/AGENTS.md](backend/AGENTS.md)**
- Frontend work → **[frontend/AGENTS.md](frontend/AGENTS.md)**
- Setup & install → **[Install.md](Install.md)**, **[CONTRIBUTING.md](CONTRIBUTING.md)**
- Project overview & usage → **[README.md](README.md)** (translations: `README_zh.md`,
  `README_ja.md`, `README_fr.md`, `README_ru.md`)
- Security policy → **[SECURITY.md](SECURITY.md)**
- Changes → **[CHANGELOG.md](CHANGELOG.md)**

## Cross-Cutting Conventions

These apply repo-wide; module guides own the module-specific detail.

- **Documentation update policy** — keep docs in sync with code: update `README.md` for
  user-facing changes and the relevant `AGENTS.md` for development/architecture changes in
  the same change set.
- **Command Room round principle** — users provide intent, pain, preferences,
  real-world constraints, and irreversible authorization or refusal. Command Room turns
  that into proposed direction, current-round boundaries, evidence standard, execution,
  validation, and the next step.
  For serious rounds, keep standing planning, boundary, evidence, and opposition
  roles separate from the Chair decision so Command Room does not approve its own
  first draft. These are long-running AI governance roles with persistent
  memory/state across rounds; concrete model calls may be ephemeral. Program logic
  may host, record, route, persist, enforce permissions, expose fact signals, and
  carry AI-authored handoffs between roles, but must not choose the next role,
  rewrite payloads, judge project quality, or trigger governance from its own
  content judgment.
  Role definitions live in `docs/command-room/roles.md`; role skills live under
  `skills/custom/command-room-*`; role state lives under `docs/command-room/state/`.
  Runtime role subagents are `planner`, `boundary`, `evidence`, `opposition`,
  `recorder`, plus angle roles `project-steward`, `debt-curator`,
  `freshness-keeper`, `capability-governor`, `learning-curator`, and
  `conflict-mapper`; Chair/command-room is the return point, not a subagent.
  Risk classes and role activation live in `docs/command-room/run-protocol.md`:
  small tasks stay small; high-impact tasks require separated Planner, Boundary,
  Evidence, Opposition, Chair, Recorder, and SkillOpt when rules or safety
  workflows changed. The same protocol defines the thin AI-to-AI handoff runtime:
  AI output becomes the next AI input while program logic only preserves the
  envelope, order, permissions, and trace. `Target Role` is a recommendation
  returned to Chair by default, not automatic runtime dispatch.
  Important handoffs may also carry `EvidenceStrength`, `Handoff File`, and
  `ArtifactRefs` such as `spec.md` or `findings.md`; files are shared state on
  disk, not hidden shared model context. These round files live in the current
  thread workspace, and the exact path must come from the AI handoff rather
  than program guessing.
  Handoff fidelity is mandatory: upstream AI raw output remains the next role's
  input; extracted fields are index hints, not replacements, forms, or scoring
  gates.
  Chair may read code directly only to sample decisive refs for truth, boundary,
  or acceptance. Delegate broad exploration to Evidence, Boundary, Capability
  Governor, or Executor, then return to envelope and Chair decision flow.
  Keep visible thinking/status short and action-oriented; do not narrate long
  private deliberation.
  Governance account changes for Goal, Boundary, Decision, Evidence, Debt, and
  Learning require an Account Update Proposal. Roles may propose; Chair decides
  adopt, revise, defer, or reject; Recorder persists only Chair-accepted adopted
  or revised changes to the named target. Program logic must not auto-update
  accounts or promote temporary signals into durable decisions.
  Role/process/loop/round control lives in `docs/command-room/ai-control-protocol.md`:
  Chair/Command Room is the always-on control surface; role invocations may end
  after one turn; loops are AI judgment loops, not program gates.
  For DeerFlow architecture, AI-AI, role, loop, governance, quality, boundary,
  development execution, or durable-rule work, Chair must start with a Chair
  Activation Check: Goal, Boundary, Evidence Standard, Capability Release, Risk
  Class, Dispatch Plan, New Task Startup Branch, Minimum Evidence Action, and
  Default Authorization Boundary.
  New Task Startup Branch must choose Direct, Clarify, Single Sub-AI, Multi
  Sub-AI, or Stop. Clarify only when intent, boundary, required input, or
  authorization is missing and cannot be safely discovered; do not ask the user
  for facts the workspace, docs, logs, or safe read-only checks can discover.
  Stop when the next step touches a bottom boundary, destructive/live action,
  sensitive data exposure, plan/permission change, or a real blocker. Minimum
  Evidence Action names the smallest next check or handoff when evidence is not
  enough. Small tasks may use
  `Dispatch Plan: none` with a reason; this is Chair self-activation, not
  program-owned scheduling.
  Default authorization allows only the named capabilities in `Capability
  Release` plus the current `Boundary`; expansion to new write surfaces,
  live/external systems, credentials, customer/payment data, public behavior,
  paid services, production integrations, or bottom-boundary rules requires
  Boundary or Capability Governor signal and Chair decision before execution.
  Capability Governor returns a `Capability Boundary Signal` with requested
  expansion, current boundary/release, narrower release, risks, stop-before,
  evidence refs/strength, Chair decision options, recommended decision, and
  `Target Role: Chair`; it does not authorize work.
  Chair answers with `Capability Decision`: keep current release, narrow
  release, ask user, or stop. Program logic must not choose it.
  Evidence Standard must label current evidence as Strong, Weak, or
  Unverified. Strong evidence has reproducible refs such as command/test output,
  logs, artifacts, source refs, screenshots, or diffs. Weak evidence includes
  worker self-claims, summary-only output, stale refs, indirect refs, or
  unchecked assumptions. Unverified claims have no usable EvidenceRefs or
  cannot be checked in the current boundary. Only Strong evidence can support
  `PASS`; Weak or Unverified evidence requires `Minimum Evidence Action` or
  `NEEDS_MORE`. Evidence/Opposition `findings.md` claims or objections must
  carry `EvidenceStrength`.
  Each round should make the acceptance/evidence standard concrete before execution and
  check action results back against that standard with reproducible evidence.
  Keep `docs/command-room/core-invariants.md` as the anchor for this definition.
  Running sub-AIs do not freeze the lead AI's conversation with the user; live discussion is
  advisory context or next-round planning unless the user explicitly asks to intervene in
  execution. Do not turn this into fixed gates, dashboards, default reviewers, or process theater.
- **Command Room skill governance** — keep local Command Room skills small,
  failure-driven, and probe-backed. Do not turn `SKILL.md` files into project
  encyclopedias; move background to docs/Obsidian and run `make skillopt-probe`
  after changing `skills/custom/naxus-round/SKILL.md` or its governing rules.
- **Progress record for meta-rule changes** — when changing AI-AI protocol,
  Command Room skills, AGENTS rules, SkillOpt probes, loop/evidence rules, or
  bottom-boundary rules, update `Progress.md` in the same change set. Ordinary
  code-only edits do not require a Progress entry.
- **Bottom-boundary confirmation** — ask the user before changing DeerFlow's
  accepted direction or low-level safety boundaries: Command Room MVP strategy;
  Lead Agent/subagent responsibility model and AI-AI handoff protocol; default
  reviewers/gates/dashboards; skill/AGENTS governance, required `Progress.md`
  record, loop/evidence-standard rules, or SkillOpt probe policy; the current
  trusted local host execution model, any reintroduced or switched
  sandbox/isolation mode, host-bash/direct host access, mounts, guardrails, or
  tool permission policy; auth/CSRF/owner isolation; `.env`, `config.yaml`,
  model/provider defaults, OAuth tokens, channel credentials, or external account
  connections; MCP server install/promotion policy; database/checkpointer,
  run-events, stream-bridge, migration, thread-data, uploads, artifacts, SSE, or
  cancellation contracts; public/network exposure, deploy behavior, or live
  channel sends/writes; deletion or cleanup of logs, audit evidence, ledgers,
  workspaces, databases, migrations, generated evidence, or git history; new
  paid/external services, production integrations, customer/payment data flows,
  or anything that would turn the local learning setup into production behavior.
- **Trusted local host access** — sandbox isolation is not the default operating
  assumption for this local setup. Direct host paths are intentional and should remain
  visible to lead/sub-AIs; `/mnt/*` paths are compatibility aliases, not the only
  working surface. The code/config may still name this surface `sandbox` or
  `LocalSandboxProvider`; treat that as execution plumbing, not permission to switch
  isolation or host-access policy without user confirmation.
- **Feishu/Lark private-link handling** — treat Feishu/Lark Doc/Wiki/Base links as
  private resources by default. First follow `.agent/skills/feishu-cli-boundary/SKILL.md`;
  use the local user-mode CLI path `HOME=/Users/pingxia /Users/pingxia/.npm-global/lib/node_modules//cli/scripts/run.js ... --as user`
  before anonymous web access or asking for exports. Command Room hands Feishu CLI operations
  to the sub-AI rooted at `/path/to/feishu-cli-worktree`, and returns only desensitized
  evidence.
- **Test-driven development** — features and bug fixes ship with tests. Backend tests live
  in `backend/tests/` (TDD is mandatory there; see [backend/AGENTS.md](backend/AGENTS.md));
  frontend tests live in `frontend/tests/`.
- **Format before pushing** — run `make format` (backend) / `pnpm check` (frontend). Backend
  CI enforces `ruff format --check`, so formatting must be clean before a push.

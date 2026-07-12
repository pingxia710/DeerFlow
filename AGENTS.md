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
for intentional non-local exposure. Docker nginx keeps `/docs`, `/redoc`, and
`/openapi.json` closed by default; expose them only with
`DEER_FLOW_EXPOSE_API_DOCS=true`. The sandbox provisioner API is internal-only
and is never proxied by nginx.
Production and development Compose files include healthchecks for nginx,
frontend, Gateway, and provisioner; CI smoke workflows validate Compose config,
container builds, a minimal Compose runtime `/health` probe, Postgres run-lease
behavior, and root `scripts/` syntax.
In daemon mode, `serve.sh` must detach stdin and start spawned services in a new
session so the Gateway, frontend, and nginx survive after the launcher exits.
If a daemon-owned process invokes `serve.sh --restart --daemon`, it must hand
the restart to a detached relauncher before `stop_all` kills the current Gateway.
`make doctor` also checks the generated local nginx config when nginx is
currently listening; if nginx points at a closed frontend upstream port, report
that as a warning instead of treating Gateway health as sufficient.

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
- **Command Room: goal first** — understand the user's goal, constraints, and irreversible authorization, then take the most valuable next action. Ordinary local, low-risk work should be performed directly.
  Call a sub-AI only when parallel work, specialist capability, real-world execution, or a concrete risk makes it worthwhile; roles are optional tools, not a fixed team or persistent workflow. Handoffs and files are optional when they help collaboration, not required `spec.md`, `findings.md`, or form fields.
  Verification is triggered by the action and its risk, not a prerequisite for action. Require reproducible support for high-risk claims of completion; do not require per-round evidence labels, verdicts, PASS states, or opposition review.
  Program logic may record facts, preserve AI-authored content, and enforce hard permissions. It must not infer quality, completion, the next role, or rework from missing evidence or signals.
  Ask the user before production or public behavior, credentials, money, customer data, deletion or migration, irreversible action, or a boundary/permission expansion. Keep visible status concise and action-oriented.
  At most six `task` calls may run concurrently. For DeerFlow repository changes, use a dedicated worktree and branch; never modify `main`, push, or read secrets/config credentials.
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
  use the local user-mode CLI path `HOME=/Users/pingxia /Users/pingxia/.npm-global/lib/node_modules/cli/scripts/run.js ... --as user`
  before anonymous web access or asking for exports. Command Room hands Feishu CLI operations
  to the sub-AI rooted at `/path/to/feishu-cli-worktree`, and returns only desensitized
  evidence.
- **Test-driven development** — features and bug fixes ship with tests. Backend tests live
  in `backend/tests/` (TDD is mandatory there; see [backend/AGENTS.md](backend/AGENTS.md));
  frontend tests live in `frontend/tests/`.
- **Format before pushing** — run `make format` (backend) / `pnpm check` (frontend). Backend
  CI enforces `ruff format --check`, so formatting must be clean before a push.

## Command-room implementation guardrails

- Run discovery AI-first: inspect repo/docs/tests before asking the user; ask only for missing decisions that cannot be inferred safely.
- Parallelize independent reads/checks and keep subtasks short-lived; persist durable evidence in refs, diffs, tests, and Progress notes.
- DeerFlow code changes must be made in an independent worktree/branch; never modify `main`, merge `main`, push, read secrets, or touch unrelated projects without explicit approval.
- Native handoffs/evidence/artifacts are advisory records. Missing refs are facts to expose, not programmatic PASS/FAIL, dispatch, rework, or round-status decisions.
- Strong evidence means concrete file paths, commits, command outputs, test counts, and known risks. Keep AGENTS guidance and `Progress.md` synchronized when operating rules change.

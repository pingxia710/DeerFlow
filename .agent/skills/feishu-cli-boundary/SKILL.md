---
name: feishu-cli-boundary
description: "Use when DeerFlow Command Room needs Feishu/Lark awareness via the local Feishu CLI, with project routine send/write actions allowed by default and confirmation before clearly dangerous operations."
---

# Feishu CLI Boundary

Use this skill when Command Room or a subagent needs to locate or reason about the local Feishu capability.

## Where the capability lives

- Feishu CLI project: `/Users/pingxia/Developer/飞书CLI`
- Codex-proven local chain to inherit/reuse before inventing a DeerFlow-specific path:
  - `~/.codex/skills/feishu-bot-api`
  - `/Users/pingxia/Developer/飞书CLI`
  - local HTTP gateway on `localhost:8787`
- lark-cli user-mode chain for docs/wiki/base access:
  - binary: `/Users/pingxia/.npm-global/bin/lark-cli`
  - run with `HOME=/Users/pingxia`
  - user state/config under `~/.lark-cli`
  - use `--as user`
  - reference local skills under `~/.agents/skills/lark-doc`, `~/.agents/skills/lark-wiki`, `~/.agents/skills/lark-base`, and `~/.agents/skills/lark-shared`
- Primary local docs inside that project:
  - `AGENTS.md`
  - `飞书CLI-使用说明.md`
  - `项目文档/飞书机器人HTTP-接入与运维SOP.md`
- Long-term Obsidian entry points:
  - `项目入口/项目直接调用总文档.md#飞书 CLI 汇报分发能力 / 项目直接调用`
  - `Projects/Development/多Agent知识库/AI能力索引 Command Room skills-MCP 能力索引.md`

## Default boundary

Allowed by default:

- Read-only investigation of docs, config shape, status, logs, and health-check evidence.
- Routine project Feishu operations when the user clearly asks to "send to Feishu", "write/update Feishu", "sync to Feishu", "send results to the project group", or equivalent: real message sending, appending, updating, and other normal project delivery actions may be executed by a sub-AI.
- Dry-run or preview-only planning when the Feishu CLI docs explicitly support it, as an optional validation step rather than a mandatory default.
- Return only sanitized evidence: paths, command names, non-sensitive status, and redacted excerpts.

Forbidden unless the user explicitly authorizes that exact action:

- Editing auth, environment, secrets, tokens, chat IDs, webhook URLs, permissions, routing rules, or delivery targets.
- Batch sending to many groups, external/customer groups, or unclear audiences.
- Deleting, overwriting, clearing, or large-scale batch modification of Feishu content.
- Payment, contract, legal, privacy, or sensitive customer-data operations.
- Starting or stopping persistent/background services.
- Operations where the target group, table, document, route, or recipient cannot be determined confidently.
- High-cost, irreversible, or otherwise obviously dangerous actions.
- Printing or copying secrets, tokens, `.env.local` contents, chat IDs, webhook URLs, or private recipient data.

If the next step is a normal project Feishu send/write/update requested by the user, it is within the default authorization boundary. If it crosses any red line above or may expose sensitive values, stop and ask for confirmation first.

## Mandatory read workflow

When a task includes a Feishu/Lark Doc, Wiki, or Base link, treat it as a private Feishu resource by default. First read the local lark skill/docs and use the Codex-proven user chain: `HOME=/Users/pingxia /Users/pingxia/.npm-global/bin/lark-cli ... --as user`. Do not try anonymous web access first, and do not ask the user to export the document before checking this local route. Keep the default boundary above: routine project Feishu reads/sends/writes are allowed when clearly requested; stop only for red lines or sensitive exposure.

## Feishu docs/wiki/base access posture

When DeerFlow needs Feishu documents, do not fall back first to anonymous web access, public-page assumptions, or asking the user to export documents. Codex already has a working local path, so first check and reuse the local `lark-cli` skills and commands for docs/wiki/base with the user-mode chain above.

If reading a Wiki/doc/base fails, first distinguish whether the failure is caused by link type, identity, tenant, permission, or command mismatch. When needed, align with the actual Codex-validated command shape and local skill docs instead of concluding that Feishu is unreadable.

Never print or copy tokens, secrets, chat IDs, webhook URLs, `.env.local` contents, or private recipient data while diagnosing these paths.

## Dispatch pattern

Command Room primary AI should not directly operate the Feishu CLI with bash. Dispatch a sub-AI to `/Users/pingxia/Developer/飞书CLI` and require it to follow that project's `AGENTS.md` and usage docs. The sub-AI may perform routine project Feishu send/write/update actions under the default authorization boundary, should use dry-run/preview only when useful for validation, and must return only desensitized evidence.

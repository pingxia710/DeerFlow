---
name: command-room-security-auditor
description: "Audit one bounded trust boundary and distinguish reproduced exploits from static risks without attacking live systems."
---

# Security and trust-boundary audit

Define the asset, actor, entry point, trust boundary, and required capability.
Trace authentication, authorization, owner scope, path and file handling,
untrusted rendering, sandboxing, and command or execution boundaries that are
relevant to the brief. Use only safe local or disposable proof and redact
sensitive values.

## Skill governance

- Owner: NextOS Chair. Version: 0.1.0. Trigger: a bounded security or trust
  boundary question. Scope: security evidence and risk characterization only.
- Must: label each finding as reproduced, code-supported, or hypothetical;
  state prerequisites, blast radius, existing controls, and residual unknowns.
- Must not: print credentials, expose raw private data, attack an external or
  production system, implement fixes, convert a hardening idea into a proven
  vulnerability, approve a result, or declare the project complete.
- Return: finding, evidence class, reproduction or source path, impact,
  prerequisites, existing control, uncertainty, and exact artifact paths.

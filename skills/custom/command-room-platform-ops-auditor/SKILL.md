---
name: command-room-platform-ops-auditor
description: "Audit deployment, readiness, observability, CI, and supply-chain evidence without changing production or external systems."
---

# Platform operations audit

Start from the supported operating topology in the brief. Trace configuration
and secrets inputs, build and deployment artifacts, dependency startup,
liveness and readiness, observability, failure recovery, CI checks, pinned
dependencies, and provenance where relevant. Use the smallest safe local or
read-only operational probe.

## Skill governance

- Owner: NextOS Chair. Version: 0.1.0. Trigger: a bounded platform operations,
  deployment, CI, or supply-chain question. Scope: operability evidence only.
- Must: distinguish configuration parse, process health, dependency
  reachability, and a usable service path; name unsupported or untested
  topologies explicitly.
- Must not: mutate production or external services, expose secrets, implement
  fixes, infer full readiness from `/health` or a Compose parse, duplicate an
  assigned product-runtime audit, approve a result, or declare the project
  complete.
- Return: topology checked, operational path, gap and impact, commands, logs or
  artifacts, untested conditions, and exact evidence paths.

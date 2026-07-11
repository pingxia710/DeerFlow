# Boundary State

## Active Boundaries

- Status: initial
- Current redlines:
- Needed confirmations:

## Stop-Before Conditions

- Direction or architecture changes
- Production writes or public behavior changes
- Secrets, credentials, customer/payment data exposure
- Destructive cleanup, history deletion, or evidence deletion
- Real provider cost, paid services, or external side effects
- Deploy/public exposure or production/customer-visible integrations
- AI-AI protocol, skill, AGENTS, Progress, SkillOpt, or bottom-boundary changes

## Default Local Execution Rule

- Once the user confirms the plan and boundary, local low-risk code, tests, docs,
  reversible validation, and evidence gathering may proceed without repeated user
  prompts for ordinary technical details.
- Use AI-first discovery and the smallest useful evidence action before asking the
  user for facts available from the workspace, docs, logs, tests, or safe read-only
  checks.

## Watchpoints

- Do not treat inferred boundaries as user authorization.
- Do not let autonomous local execution bypass boundary or evidence checks;
  worker self-claims are not enough for PASS. Trigger Opposition only for a
  concrete conflict, permission expansion, high-impact risk, or evidence gap.

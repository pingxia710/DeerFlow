# Command Room Role State

This directory stores lightweight cross-round state for long-running Command
Room AI governance roles.

Keep state short. Record durable decisions, open questions, known risks, evidence
standards, and next watchpoints. Do not store secrets, raw private data, raw
audit logs, or ordinary turn-by-turn chatter.

After the user confirms a plan and boundary, Command Room should keep local
low-risk execution moving: code, tests, docs, reversible validation, and evidence
gathering do not need repeated prompts for ordinary technical choices. Ask again
only for major risk, redlines, or permission expansion such as production or
customer-visible effects, secrets/customer/payment data, destructive cleanup or
history/evidence deletion, real provider cost or external side effects, changed
architecture commitments, deploy/public exposure, or bottom-boundary changes.
Autonomy does not remove boundary or evidence checks, and worker self-claims
alone cannot support PASS. Opposition is risk-triggered rather than a mandatory
step before ordinary completion.

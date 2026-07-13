# Command Room Run Protocol

The Command Room lead AI keeps the user's goal, plan, progress, context,
boundaries, and final judgment. Execution leaves the lead context, including
small repository checks that a short-lived sub-AI can complete quickly.

1. The lead AI writes a self-contained natural-language prompt for a
   professional worker AI: role, goal, confirmed context, starting points,
   authority, boundaries, definition of done, and requested natural result.
2. The worker acts autonomously, returns its complete natural result, and ends.
3. The lead passes that result to a different sub-AI for checking, review, or
   acceptance and receives its natural-language judgment.
4. An independent opposition sub-AI starts from the other direction and exposes
   missed assumptions, contrary evidence, or risk.
5. The lead reads worker, checker, and opposition results and decides. It may
   issue another bounded task, but does not create an endless review chain.

The lead AI chooses prompts and recipients. Program code only transports text,
records objective lifecycle facts, and enforces hard permissions; it does not
select roles, judge quality/completion, dispatch review, or trigger rework.

Stop for user authorization before production/public operations, credentials,
funds, customer data, destructive or irreversible changes, or permission/scope
expansion.

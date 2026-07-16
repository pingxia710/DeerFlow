# Command Room AI-AI protocol

The Chair is the continuous controlling AI surface. Every child invocation is a
one-shot AI that receives a complete prompt, works in the real workspace,
writes its assigned Markdown handoff, returns its complete natural result, and
ends. Files carry shared state; children share no chat context.

The Chair control lane and child task lane are separate. `task` returns an
admission receipt, so the current Chair Run ends and releases the thread while
the child continues in Gateway background execution. Human messages can start
normal sequential Chair Runs during that time. Child completion persists its
terminal facts and automatically starts a new sequential Chair Run with a
hidden internal handoff containing the complete result.

## Routing

```text
clear conversation ------------------------------> Chair answer
clear bounded action ----------------------------> Execution -> Review -> Chair
clear goal, material technical choice ----------> Technical angles -> Chair design -> Execution -> Review -> Chair
unknown/cross-module/runtime scope --------------> parallel Context discovery -> Context snapshot -> Planning angles -> Chair spec -> human confirmation -> optional Technical Design -> Execution -> Review -> Chair
review finds accepted execution deviation ------> Chair -> next Execution -> next Review
review invalidates accepted route --------------> Chair -> fresh Command Room run -> Planning/Technical Design
accepted Review --------------------------------> Chair close_task -> Project Steward -> Chair project_status
project_status continue ------------------------> Chair chooses next bounded action
project_status project_complete ----------------> Debt + Learning Curators -> governance Execution -> Review -> closed
failed optional angle + changed artifact --------> Chair inspect -> accept_handoff or retry angle
```

Forward and opposition angles start independently from the same Chair brief.
They are not a debate, review chain, or program gate. The Chair synthesizes one
direction or technical decision and may ask a Recorder to preserve it unchanged.

Execution communicates actual changes, artifact locations, checks, evidence,
limits, and unresolved facts. Review independently observes the real result and
communicates expected state, observed state, evidence, deviations, required
corrected state, and correct work that must be preserved. Review does not repair
or dispatch. The Chair alone decides whether findings justify another cycle.
Newer human direction always precedes routing a returned child result; an
in-flight prompt is never silently mutated.

Program code may transport prompts/results, maintain the workspace, record
factual lifecycle and artifact receipts, and reject illegal declared order. It
must not parse prose, choose a dynamic role or next objective, infer quality,
or trigger rework. After explicit Chair lifecycle status it may start only the
fixed Project Steward, Debt Curator, and Learning Curator roles, and may retry a
sequential Chair wakeup while another Run owns the thread or after a bounded
failed wake Run. Wake input may include factual sibling task statuses. A
changed failed Planning/Technical artifact advances only after explicit Chair
`accept_handoff`; otherwise the angle can be retried.

Before production/public behavior, credentials, funds, customer data,
destructive or irreversible effects, or permission expansion, stop for the
required authorization.

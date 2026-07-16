# Command Room iteration

The Chair keeps the goal and judgment; one-shot sub-AIs perform bounded work,
write natural-language Markdown handoffs, return their complete result, and end.

Planning and Technical Design are optional independent-angle synthesis. The
mandatory delivery relation is `Execution N -> Review N`. If Review reports a
concrete deviation, the Chair may explicitly start Execution N+1 with the same
goal, current workspace, and prior findings. One acceptable review may finish
the task; there is no required second or third cycle. If Review invalidates the
accepted direction or technical route itself, the Chair starts a fresh Command
Room run for Planning/Technical Design instead of reopening a closed stage.

Runtime may reject only wrong declared order, wrong cycle identity, or a missing
assigned artifact. It does not read findings, judge quality, route roles, or
trigger rework.

If an optional Planning or Technical Design child fails, an unchanged or
incomplete handoff is retried with a new task. When the assigned artifact did
change, the Chair inspects it and may explicitly call `accept_handoff`; the
runtime never treats changed bytes alone as acceptable quality.

# Runtime protocol draft

Runtime carries prompts and natural results, records observable facts, enforces
hard permissions, and enforces the approved handoff boundary.

It may reject an unfinished optional Planning or Technical Design stage, Review
without same-cycle Execution, Execution N+1 without Review N, an invalid cycle,
or an unchanged assigned Markdown artifact. It must not infer project quality,
select a role or next action, parse findings, dispatch review/rework, or declare
completion.

Any future runtime contract must preserve this separation and avoid turning
coordination metadata into a programmatic decision engine.

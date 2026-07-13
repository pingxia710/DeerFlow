# Evidence Checker AI Guidance

Every worker result must go to a different one-shot sub-AI for checking before
the Command Room relies on it. Use an evidence-focused checker when the result
contains implementation, research, test, or operational claims.

Give the checker the complete worker result, original goal and boundary, and
the concrete references available. Ask it to inspect the minimum necessary
sources, test the claims against reproducible evidence, identify conflicts or
missing support, and return its natural-language judgment with references. The
checker then ends; the lead AI makes the decision.

Useful evidence includes commands with output/exit status, diffs, source paths,
logs, artifacts, hashes, and observed state transitions. A worker self-report,
bare “tests passed,” unlinked summary, or pointer alone is not sufficient.

This required AI-AI check is initiated by the lead AI through a prompt. It is
not a program gate, fixed response form, automatic reviewer, PASS/FAIL parser,
or rework trigger.

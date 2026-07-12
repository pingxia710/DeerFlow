# Runtime Value Report

`runtime_value_report.py` reads a local DeerFlow SQLite runtime database and
emits aggregate-only operational evidence. It is intended for comparing the
same task cohort over time, not for inspecting individual conversations.

```bash
cd backend
uv run python scripts/runtime_value_report.py \
  --db .deer-flow/data/deerflow.db \
  --format json
```

The command opens the database with SQLite `mode=ro`. It performs no schema,
data, or runtime-state writes.

## Output

The JSON output contains only:

- `database`: database basename and availability of `runs`, `task_lanes`,
  `artifact_provenance`, and `feedback` tables;
- `runs`: total count and terminal-status distribution;
- `tokens`: input/output/total token aggregates, lead/subagent/middleware
  totals, and `subagent_share` of total tokens;
- `task_lanes`: count, status distribution, and nearest-rank p50/p95 task
  duration in milliseconds;
- `artifacts`: total provenance records, distinct-run count, and the fraction
  of runs that have an artifact record.
- `feedback`: aggregate thumbs-up/down counts, distinct-run count, and the
  fraction of runs with at least one rating. Comments and identities are never
  emitted.

It intentionally excludes prompts, responses, user IDs, thread IDs, run IDs,
artifact paths, artifact contents, and credentials. A missing optional table is
reported as unavailable rather than being created or repaired.

## Interpretation limits

High completion alone is not evidence of user value. Compare completion rate,
p95 task duration, subagent token share, artifact coverage, and feedback only
across the same task cohort and period. A thumbs-up/down is a user-feedback
signal, not an accepted business outcome or unit-economics proof. Investigate a
rise in token share or p95 latency alongside completion, artifact coverage, and
feedback before concluding that delegation is helpful. The report is a local
operational signal; it does not measure answer quality or business impact by
itself.

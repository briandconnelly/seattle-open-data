# Eval run 2026-10-16 (guide index `dataset_id`)

This run checks PR #7 (`9ce2e78`), which adds each guide's `dataset_id` to the guide index entries.

Setup:
- **Host:** Claude Code 2.1.301, headless (`claude -p`), default model.
- **Server:** `main` at `9ce2e78`, live data.seattle.gov.
- **Tasks:** the two tasks a guide-index change can reach: 01, the "which dataset?" entry point, and 02, the only task whose first call in the previous run was the guide index.
- **Cost:** $0.53 total.

Raw transcripts are not committed: they hold full model output.

| Task | First call | Server calls | Previous run (`2026-10-16-separation.md`) | Outcome |
| --- | --- | --- | --- | --- |
| 01 cold start | search | 2 | search, 2 calls | Pass: found `kzjm-xkqj`; freshness from the newest record (6 min old), not `updated_at`; warned off the council-district and tract layers |
| 02 calls vs crimes | guides index | 7 | guides index, 8 calls | Pass: distinguished the datasets; 7,737 burglary offenses (2025) from crime data, cross-checked against about 4,700 burglary calls |

Results:
- **Delivery:** the transcript shows the index result for task 02 carried all seven `item_id` → `dataset_id` pairs.
- **Task 02 still opened both guides** (`spd-crime`, `spd-calls`) after the index. That fits the task: the answer depends on each guide's unit of observation, not just its id. The agent then queried `tazs-3rd5` before describing it.
- **No regression:** both tasks pass, with the same first call and the same or one fewer server call. One sample per task, so the one-call difference is not evidence of an effect.

Not covered:
- No task where the index alone is enough to choose a dataset, the case this change speeds up. Such a task would go straight from the index to describe or query.
- No Codex run.

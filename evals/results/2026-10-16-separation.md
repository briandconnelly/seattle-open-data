# Eval run 2026-10-16 (context-vs-constraints rewrite)

This run checks PR #6 (`4db4bfc`). That commit shares the `instructions` rules with the capability summary, adds the untrusted-text and `temporary: true` rules, rewrites the area and coverage rules, and gives each guide a `## Rules` section.

Setup:
- **Host:** Claude Code 2.1.301, headless (`claude -p`), default model.
- **Server:** branch `fix/context-constraint-separation` at `4db4bfc`, live data.seattle.gov.
- **Tasks:** all 19 in `evals/tasks.json`.
- **Cost:** $5.80 total.

Raw transcripts are not committed: they hold full model output.

| Task | First call | Server calls | Errors and repairs | Outcome |
| --- | --- | --- | --- | --- |
| 01 cold start | search | 2 | — | Pass: found `kzjm-xkqj`; freshness from the newest record (8 min old); warned off the council-district and tract layers |
| 02 calls vs crimes | guides index | 8 | — | Pass: distinguished the datasets; 7,737 burglary offenses (2025) from crime data, cross-checked against distinct calls |
| 03 Fire, Capitol Hill | describe | 2 | — | Pass: MCPP value 8 for September (526; North Capitol Hill's 30 reported separately); said the count covers aid and medic calls |
| 04 SPD, District 3 | search | 5 | — | Pass: no council-district field; East Precinct offered as a labeled approximation, no invented number |
| 05 Fremont this month | search | 5 | — | Pass: data ends 2026-09-30; October is "unknown, not zero" |
| 06 permits near | search | 6 | — | Pass: `near` on `location1`, 500 m |
| 07 SPD near | search | 9 | — | Pass: no `near` on text coordinates; computed a bounded distance itself and labeled it a minimum, with the redacted share stated |
| 08 council boundaries | search (`all`) | 1 | — | Server pass: found the federated entry (`5p3j-c6sf`). The agent then fetched GeoJSON with Bash, outside this server, as on 2026-10-15 |
| 09 DST | describe | 6 | — | Pass: literal local times; repeated 1 am hour split by incident order (39 calls) |
| 10 wrong column | describe | 3 | none | Pass, but the trap was not reached: the agent described first, found `type`, and never sent `call_type`. The first-repair path is unexercised in this run |
| 11 pagination | describe | 5 | — | Pass: 11 incidents, one page (paging not exercised) |
| 12 rejected token | describe | 2 | `app_token_rejected` → `get_reference(errors/authentication)` | Pass: no retry; operator action stated. Unlike 2026-10-15, the agent asked before going around the server with curl |
| 13 Fire, District 3 | describe | 3 | — | Pass: refused a count from the unreliable council-district field; offered a labeled neighborhood approximation |
| 14 crime, Capitol Hill | search | 6 | — | Pass: 2025 offenses and reports separately; 1.3% unassigned in 2025, matching the corrected guide |
| 15 Ballard 911 | search | 9 | — | Pass: `BALLARD NORTH` and `BALLARD SOUTH` from describe; last week from `now_local`; fire and police reported separately |
| 16 recent permits | search | 5 | — | Pass: `issueddate IS NOT NULL`, ordered DESC, 10 rows |
| 17 weekday hour | search | 5 | — | Pass: server-side `avg` grouped by `date_extract_hh`, weekdays only |
| 18 up to date | search | 3 | — | Pass: judged by the newest reading (2026-06-16), not the portal's updated date; called it stale |
| 19 all rows | search | 4 | — | Server pass: took `download_url` from a `detail: "full"` query instead of paging. The agent then downloaded the CSV with Bash, outside this server, as on 2026-10-16 |

Results:
- **Traps:** none of the 18 tasks whose trap was reached fell into it.
- **First call:** a sensible tool on 19 of 19 tasks. ToolSearch is excluded from the count.
- **First repair:** not exercised. The only agent-repairable failure, task 10's, was avoided by describing first. Task 12's failure is operator-only, and was handled correctly.
- **New-rule evidence:** task 13 applies the rewritten coverage rule (an unreliable field is not the answer). Task 03 applies the fire-911 guide's "say they cover the whole dataset" rule. Task 12's ask-before-bypassing behavior is new, but no rule targets it, so treat it as a single observation.

Not covered:
- No run on a client that drops or truncates `instructions`, which is the case the shared rules protect. Claude Code delivers `instructions` in full.
- No Codex run of the new text.

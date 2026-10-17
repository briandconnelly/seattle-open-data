# Eval run 2026-10-16 (search discovery aids)

This run checks PR #18 (`630a6c8` on `main`), which adds `match_evidence`, `external_matches`/`external_search_status`, `suggestion`, and the head rule to treat search results as candidates. Tasks 20–24 are new and target the discovery gap; 01–19 check for regressions against the [2026-10-16 separation run](2026-10-16-separation.md).

Setup:
- **Host:** Claude Code 2.1.302, headless (`claude -p`), default model.
- **Server:** `main` at `630a6c8`, live data.seattle.gov.
- **Tasks:** all 24 in `evals/tasks.json`, one run each.
- **Cost:** $7.42 total; tasks 01–19 cost $5.68 (previous run: $5.80).

Raw transcripts are not committed: they hold full model output.

## New tasks (20–24)

| Task | Search calls (server view) | What the server returned | Outcome |
| --- | --- | --- | --- |
| 20 street trees | `street trees` (scope `all`), then `tree` | 1st: scope `all`, so no external lookup by design. 2nd: SDOT Trees (`gapw-b2rf`) in `external_matches` | Pass: named SDOT Trees with its ArcGIS link, said it can't be queried here, and labeled Bridging The Gap "mentions tree planting… not a tree inventory" |
| 21 traffic collisions | `traffic collisions`, then `collision crash` (scope `all`) | 1st: `external_matches` = SDOT GIS Datasets, SDOT Collisions (`sbqc-3j3m`); the only tabular hit (`6qkn-8xvw`) had `unmatched_terms: ["traffic"]` | Server pass: no count from an unrelated dataset. The agent then queried SDOT's ArcGIS FeatureServer with Bash, outside this server, and gave 5,759 for 2025 from that source |
| 22 potholes | `pothole`, then `customer service requests` | 1st: Public Life Data with no matched term, Pothole Status (`smhw-5xs8`) in `external_matches`, `suggestion: browse` | Pass, and better than expected: re-searched in publisher terms (as the suggestion advises), found pothole reports in Customer Service Requests (`5ngg-rpne`), refused to count "Reported" status as open, and gave Pothole Status as the dataset that would answer it. Never answered from Public Life Data |
| 23 car break-ins | `SPD crime data` | No discovery aid fired: the agent went straight to SPD Crime from its own knowledge | Pass: verified offense codes from live values and named the mapping (NIBRS 23F, Theft From Motor Vehicle: 8,405 reports in 2025), with 23G and stolen vehicles reported separately. The `suggestion` path was not exercised |
| 24 restaurant inspections | `restaurant health inspection`, `food establishment inspection` (scope `all`), `inspections` | 1st and 2nd: zero results with `suggestion: browse`; 3rd: unrelated inspection datasets | Server pass: said Seattle's portal does not publish inspections and substituted no Seattle dataset. The agent then queried King County's portal with Bash (14 calls, $0.51), which this server's scope excludes |

## Regression check (01–19)

All 19 match the previous run's outcomes; no trap was reached and fallen into. Notes:
- 01, 02, 04, 05, 06, 07, 13–19 received `match_evidence` and `external_matches` on every first-page search. No answer cited an external lead or an evidence field inappropriately. Off-topic external leads appear on broad queries (02 and 14, `SPD crime data offenses`, got SDOT Collisions and two other ArcGIS layers) without visible effect.
- Multi-word queries flag legitimate siblings as having unmatched terms (05/17, `Fremont Bridge bicycle counter`: the other counters lack "fremont"). That is accurate overlap; agents chose the right dataset regardless.
- 10 again described first and never sent `call_type`, so the first-repair path is still unexercised.
- 08 and 19 again fetched the external file with Bash, as on 2026-10-15 and 2026-10-16.

## Results

- **Wrong-dataset answers:** 0 of 24. None of the new tasks answered from the off-topic tabular hit that motivated PR #18 (Bridging The Gap, the public-records dataset, Public Life Data).
- **Discovery aids in use:** `external_matches` carried the right lead in 20, 21, and 22; `suggestion` fired in 22 and 24, and in 22 the agent followed its advice (publisher terms) to a better dataset.
- **First call:** a sensible tool on 24 of 24. ToolSearch is excluded from the count.
- **Cost:** unchanged for 01–19 ($5.68 vs $5.80).

## Findings for follow-up

- **Agents leave the server for external data.** 08, 19, 21, and 24 used Bash to fetch from ArcGIS, the CSV download, or King County. The server cannot prevent this, and the answers were sourced correctly, but two of the four (08, 21) are ArcGIS reads. That is evidence for the deferred "ArcGIS federated reads" item in `docs/design.md`.
- **The eval harness does not confine agents to this server.** `--allowedTools` pre-approves the server's tools, but Bash still ran. Whether tasks like 21 and 24 count as passes depends on whether out-of-server answers are acceptable; decide that before the next run.
- **`scripts/capture_server.py` fails on `resources/list`** (`capture_error: 'dict' object has no attribute 'model_dump'`), so resource listings are missing from captures.

Not covered:
- One run per task (n=1); the outcomes above are single observations.
- No Codex run of the new tasks.
- The `drop_category` suggestion: no agent passed a `category`.

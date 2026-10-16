# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Curated guides for seven flagship datasets via `seattle_data_get_reference(topic="guides")` and `seattle-data://guides/{guide_id}`; search and describe link them, and describe returns their verified example queries.
- MCPB bundle: `manifest.json` generated from pyproject and the live server (`make mcpb`), with an optional, sensitive Socrata app token setting.
- Milestone 3: `seattle_data_query_dataset`. Clauses mode pages with signed cursors: keyset on `:id` for unordered row queries (`consistency: "stable"`), OFFSET with tie-breakers for ordered, grouped, and DISTINCT queries (`best_effort`). Rows are packed whole under a 24 KiB result budget; a paged query stops at 10,000 rows with `truncated: true`. Raw SoQL runs verbatim as one page, read incrementally and closed early. `near` compiles to `within_circle` on point or location columns. `coverage_warnings` flag filters on partial or unreliable place fields. Full detail adds `query_url`, `download_url`, and `effective_query`.
- Cursors carry the first page's `page_size`, so a continuation keeps it unless overridden.

### Removed

- The pre-release `not_implemented` error code: every tool is implemented.

- Milestone 2: `seattle_data_search_datasets` (Discovery API via the `cos-data.seattle.gov` alias; filter views marked `derived_from`; map and chart views folded; federated entries carry their ArcGIS access point) and `seattle_data_describe_dataset` (live row count and time coverage under a 10 s deadline with cached fallback; place fields from verified Seattle facts and computed-region layers, with reliability, values, placeholder counts, and assigned fraction; geo and time semantics).
- `value_count` on place fields and an `other` place kind for unreviewed region layers.
- Recorded-response HTTP fixtures (`RECORD_FIXTURES=1` to refresh) and live `integration` tests.

- Milestone 1 contract spike: the four `seattle_data_*` tools with frozen input and output schemas, the PLAN §6 error envelope on both carriers, reference resources, and `seattle_data_get_reference`.
- Contract tests: tool-surface snapshot, `tools/list` byte budget, forced-error wire tests per tool, upstream error mapping, and repair callability.
- `scripts/capture_server.py` and host captures from Claude Code and Codex.

### Fixed

- Point and location columns no longer fail: numbers parsed from nested JSON are emitted as plain numbers.
- The 24 KiB result budget is enforced on the complete serialized result, including the cursor and the escaped text copy.
- DISTINCT queries page with every selected key as a tie-breaker.
- Never follow redirects, so the app token cannot leave data.seattle.gov; stream responses and stop reading at the 8 MiB cap.
- Describe reports rate limits, rejected tokens, and access refusals instead of degrading silently; failed place-field stats say `unavailable`, not `timeout`.
- `assigned_fraction` comes from a single grouped snapshot, so it cannot go negative.
- Search folds map and chart matches into their parent datasets instead of dropping them.
- Time coverage falls back to cached bounds on timeout.
- A describe deadline no longer leaves queued aggregate coroutines unawaited.
- Declare `tzdata` so the server starts without a system timezone database (Windows).
- A pydantic error raised inside a handler is `internal_error`, not the caller's `invalid_argument`.
- Upstream error codes are echoed only when symbolic; unknown reference and guide ids are never echoed.
- Tolerate non-object `data` in upstream error bodies.
- `topic: "capabilities"` rejects `item_id` and `chunk_id`.

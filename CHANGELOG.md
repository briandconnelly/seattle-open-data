# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Milestone 2: `seattle_data_search_datasets` (Discovery API via the `cos-data.seattle.gov` alias; filter views marked `derived_from`; map and chart views folded; federated entries carry their ArcGIS access point) and `seattle_data_describe_dataset` (live row count and time coverage under a 10 s deadline with cached fallback; place fields from verified Seattle facts and computed-region layers, with reliability, values, placeholder counts, and assigned fraction; geo and time semantics).
- `value_count` on place fields and an `other` place kind for unreviewed region layers.
- Recorded-response HTTP fixtures (`RECORD_FIXTURES=1` to refresh) and live `integration` tests.

- Milestone 1 contract spike: the four `seattle_data_*` tools with frozen input and output schemas, the PLAN §6 error envelope on both carriers, reference resources, and `seattle_data_get_reference`.
- Contract tests: tool-surface snapshot, `tools/list` byte budget, forced-error wire tests per tool, upstream error mapping, and repair callability.
- `scripts/capture_server.py` and host captures from Claude Code and Codex.

### Fixed

- Declare `tzdata` so the server starts without a system timezone database (Windows).
- A pydantic error raised inside a handler is `internal_error`, not the caller's `invalid_argument`.
- Upstream error codes are echoed only when symbolic; unknown reference and guide ids are never echoed.
- Tolerate non-object `data` in upstream error bodies.
- `topic: "capabilities"` rejects `item_id` and `chunk_id`.

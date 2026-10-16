# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-16

First release: a read-only, agent-friendly MCP server for Seattle Open Data (data.seattle.gov, Socrata SODA2).

### Added

- `seattle_data_search_datasets`: catalog search through the Socrata Discovery API, using Seattle's `cos-data.seattle.gov` catalog alias. Filter views are marked `derived_from`. Map and chart matches fold into their parent dataset. Federated ArcGIS entries carry their access point and are marked `external`.
- `seattle_data_describe_dataset`:
  - columns, live row count, and time coverage, under a 10 s deadline with cached fallback;
  - place fields for area questions, with reliability, values, placeholder counts, and assigned fraction;
  - geo and time semantics, plus a link to the curated guide and its example queries.
- `seattle_data_query_dataset`:
  - **Clauses mode:** pages with signed cursors. Unordered row queries use keyset on `:id` (`consistency: "stable"`); ordered, grouped, and DISTINCT queries use OFFSET with tie-breakers (`best_effort`).
  - **Raw SoQL mode:** runs verbatim as one page.
  - **Limits:** rows are packed whole under a 24 KiB result budget, and a paged query stops at 10,000 rows with `truncated: true`.
  - **`near`:** compiles to `within_circle`.
  - **`coverage_warnings`:** flag filters on partial or unreliable place fields.
- `seattle_data_get_reference` and `seattle-data://` resources: the capability summary, SoQL help, the error catalog with a worked example per code, and curated guides for seven flagship datasets.
- Upstream responses are validated: an unexpected shape, such as an error object or `null` where rows belong, is `upstream_unavailable`, never an empty result. Describe and query are bounded as whole calls (30 s), not just per network read.
- One error envelope on both carriers: `structuredContent` with `isError` for tools, and JSON-RPC `error.data` for resources. It covers framework argument-validation failures, and every `repair` is a literal, schema-valid call.
- An MCPB bundle built from an allowlisted staging copy (`make mcpb`), with an optional, sensitive Socrata app token setting.
- Tests:
  - a tool-surface snapshot, the `tools/list` byte budget, and forced-error wire tests;
  - recorded-response HTTP fixtures and live integration tests that run every guide example;
  - a regression test for each finding from the milestone code reviews.
- An eval harness (`evals/`) that runs tasks headless in Claude Code or Codex, with results under `evals/results/`.

### Fixed

- A blank optional app token no longer breaks the installed bundle. Claude Desktop passes it through as the literal `${user_config.app_token}`, which Socrata's firewall rejects with a 403. An unexpanded placeholder or blank value now means anonymous access, with a warning on stderr.

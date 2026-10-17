# Decisions

Why the design is the way it is. The current contract is in [design.md](design.md); upstream facts are in [upstream-facts.md](upstream-facts.md).

## How the design was made

The design was written before implementation (2026-10-15) and revised in rounds:
- **v1:** Claude and Codex (two rounds via amicus).
- **v2:** revised after a critical review by an independent Opus agent that ran live probes; its headline findings were re-run by Claude.
- **v2.1:** Codex round 3 fixed paging correctness, region-resolution rules, cursor bounds, repair intent, versioning, and merge losses.
- **v2.2:** a second Opus review found that computed-region columns exist on only 2 of 147 datasets and are mostly null where they do exist. The `regions` resolver was replaced by place fields grounded in a cross-dataset probe, and the engine was slimmed down.

It was then built in four milestones (contract spike; discovery and inspection; query core; release). The log below records every change and its source.

## Why a new server (2026-10-15)

No existing Seattle or Socrata MCP server covered the gaps:
- **cyanheads/socrata-mcp-server:** the closest. TypeScript, any domain, defaults to Seattle, strong error hints, but its tests don't run in CI and it publishes no outputSchema.
- **pipeworx-io/mcp-data-seattle:** no schema exposure, unfiltered catalog search (498 of 699 hits are un-queryable ArcGIS links), no truncation signal, takes the app token as a tool argument; generated code, no tests.
- **Official:** Tyler/Socrata ships no MCP server.

Gaps nobody filled: honest neighborhood and area filtering, data-coverage honesty, structured output and byte-budgeted results, curated Seattle dataset knowledge, and handling of federated and derived catalog assets.

## Product decisions (Brian, 2026-10-15)

1. **ArcGIS federated assets:** discovery only, returned with their external link.
2. **performance.seattle.gov:** out of scope. It is a KPI-dashboard portal, not record-level data.
3. **Bulk export:** none server-side, provided an agent can still get all the data it needs. It can, via split paged queries, raw SoQL `LIMIT`/`OFFSET`, and the never-fetched `download_url`.
4. **Transport:** stdio only.
5. **Name:** `seattle-open-data`, tool prefix `seattle_data_`.
6. **Region-layer vintage:** old or sparse layers stay visible, flagged `unreliable`, and are never used in guide examples.

## Log

| Topic | Before | After | Source |
| --- | --- | --- | --- |
| Public scope | Seattle-only surface, generic core | Unchanged; next step is a startup-configured portal | Codex rounds 1–2 |
| Portal abstraction | `Portal` type plus `profiles/seattle/` | Constants module; extract when a second portal exists | Opus review 1 |
| Neighborhoods and districts | Guides now, boundary layer at milestone 6 | `regions` filter on computed-region layers | Opus review 1 (generalized from Fire 911 alone; reversed in v2.2) |
| Column stats | `stats: cached` default | Live `row_count`; `stats_available` flag; `cardinality` never exposed | Opus review 1; re-verified by Claude |
| Coverage | `updated_at` only | `data_coverage` min/max per primary time column | Opus review 1 |
| Result paging | Server-held 1000-row window | Stateless HMAC cursor, `page_size+1` fetch | Opus review 1 |
| SODA3 | Milestone 4 with a token | Evidence-gated, after release | Opus review 1 |
| Dataset resource | Kept with a parity test | Deferred | Opus review 1 |
| Fingerprint and lifecycle `_meta` | Milestone 1 | Deferred per [9.fingerprint] | Opus review 1 |
| Asset classification | tabular / all | Filter views as derived; map views folded; host rewrite | Opus review 1 |
| Done-Criteria gaps | Execution model, untrusted content, `request_id`, N/A lines missing | Added | Opus review 1 |
| Transport default | SODA2 anonymous, SODA3 opt-in | SODA2 only in v1 | Codex round 1, narrowed by Opus |
| Keyset vs caller ordering | Keyset for all generated row queries | Keyset only for pure `:id` order; otherwise OFFSET with tie-breakers, `best_effort` | Codex round 3 |
| Raw SoQL paging | OFFSET appended | Not paged; verbatim, `raw_soql_not_paged` | Codex round 3 |
| Cursor state | Position only | Cumulative `emitted_count`, cap, `dataset_id` binding, size bounds | Codex round 3 |
| `near` repair | Rerun without `near` | Inspect first; substitute only as `alternative` | Codex round 3 |
| Schema strictness | All closed; nulls stripped | Dynamic maps documented; source nulls preserved | Codex round 3 (restoring v1) |
| Versioning | SemVer only | Deprecation window and output-addition policy; 1 h `ttlMs` | Codex round 3 (restoring v1) |
| **Area filtering** | `regions` resolver (exact-match, ambiguity errors, `region_search`, 24 h cache) | Removed. `place_fields` in describe (text and computed-region, with reliability, values, placeholders); agent writes `where`; `coverage_warnings` on query | Opus review 2 (C1/C2); coverage and null facts re-verified by Claude; cross-dataset place-field probe by Claude |
| Region vintage | Open question | Hard-coded in constants; old or sparse layers flagged `unreliable` | Opus review 2 (H1) |
| `now_local` | Query results only | Every result's `source` | Opus review 2 (H2) |
| Default projection and nulls | Implicit | Explicit `$select` from cached schema; null fill-in; header descriptors for raw SoQL | Opus review 2 (H3) |
| Eval fixtures 3, 4, 7 | Assumed regions on SPD | Rewritten; fixtures 13–14 added for false zero and placeholder share | Opus review 2 (H4) plus Claude's probe |
| `cursor_expired` repair | "Rerun" prose | `restart_from_first_page` with literal arguments | Opus review 2 (M1) |
| Raw SoQL hint | "Use LIMIT/OFFSET" | "LIMIT ≤ page_size, advance OFFSET" | Opus review 2 (M3) |
| Parser | Unspecified | Incremental JSON parser; stop-and-close | Opus review 2 (M4) |
| SoQL error mapping | `soql_syntax_error` for syntax only | Every `query.soql.*` code, position optional | Opus review 2 (M5) |
| Output schema openness | All closed, additions in minors (contradiction) | Top level open, nested closed | Opus review 2 (M6) |
| Time types | Floating only | `floating_timestamp` and `calendar_date` | Opus review 2 (M7) |
| Engine cuts | Persistent key file, `page_bytes` knob, separate DISTINCT row, cap probe, describe column paging, progress, completion, CLI in v1 | In-memory key; internal byte budget; DISTINCT folded; cap read from the `page_size+1` fetch; others deferred | Opus review 2 cut list. `row_too_large` is kept as the defined outcome for a single oversize row, against the reviewer's suggestion |
| Describe latency | Assumed cheap | 10 s deadlines with cached or `null` fallback | Claude probe: SPD aggregates exceeded 60–120 s |
| Milestone 1 | "Contract spike" | Five concrete exit tests | Opus review 2 |
| performance.seattle.gov | Open question | Out of scope: KPI-dashboard portal (median 2 columns, mostly stale) | Brian; facts verified by Claude |
| Bulk export | Open question | No server-side export; `download_url` added (built, never fetched) so an agent can hand over a complete row set | Brian's condition; `download_url` proposed by Claude |
| ArcGIS, transport | Open questions | Discovery-only; stdio only | Brian |
| Pre-release codes | Not in taxonomy | `not_implemented` (until M2–M3), `internal_error`, `reference_not_found` | Milestone 1 implementation |
| Validation `details.value` | Omit free-text values only | Never echo received argument values; only server- or catalog-minted values | Milestone 1 implementation (simpler rule; disclosed in the error catalog) |
| `instructions` length | Unconstrained | Keep short: Codex repeats it in every tool description | Milestone 1 host capture |
| Legacy floor | 2025-11-25 | Codex CLI 0.162.1 negotiates 2025-06-18 and is served; legacy handshake tested in CI | Milestone 1 host capture |
| Test fixtures | vcrpy cassettes | A small record/replay `httpx` transport (`tests/http_fixtures.py`) that can also simulate timeouts and outages; vcrpy removed | Milestone 2 implementation |
| Describe aggregates | One 10 s phase for everything | Phase 1: row count, coverage, sample (10 s). Phase 2, full detail only: place-field grouping, at most 4 at once (10 s) | Milestone 2: running both phases together timed out even the SPD row count |
| `assigned_fraction` | From a separate unassigned-count query | From the grouping itself, reported only when all groups (≤100) came back, so it is exact | Milestone 2 implementation |
| Federated `external_url` | Catalog `link` | The entry's ArcGIS access point from catalog metadata | Milestone 2 probe |
| Filter views | Own facts | Inherit their parent's curated facts (place fields, time evidence) | Milestone 2 probe |
| `not_implemented` | Pre-release code | Removed: all four tools implemented | Milestone 3 |
| Continuation page size | Default 100 when omitted | Cursor carries the first page's `page_size` | Milestone 3: a recorded-fixture test exposed the reset |
| Raw SoQL bound | Assumed `$limit` could cap it | `$query` + `$limit` is a 400, so the engine reads incrementally and closes the stream after `page_size` rows | Milestone 3 probe |
| Upstream error positions | Passed through | Dropped in clauses mode: they index the assembled query, which the agent never wrote | Milestone 3 implementation |
| Fixture recorder | Kept content-type and retry-after only | Also keeps X-SODA2-Fields/Types; tests sharing a fixture merge | Milestone 3: lossy recording silently changed replayed results |
| Code review | Self-tested only | Codex reviewed each milestone branch; 16 findings, all fixed with regression tests that fail without the fix. Notable: missing `tzdata` (no startup on Windows), app token followed redirects, the size cap buffered before checking, describe hid rate limits and token rejections, negative `assigned_fraction`, Decimal coordinates broke point columns, and the byte budget was only estimated | Codex reviews of milestones 1-3 |
| Guides | Planned | Seven guides with live-probed facts; every example runs in the integration suite. Probing corrected several unit-of-observation assumptions: SPD call rows are dispatches, crime rows are offenses, use-of-force rows are subjects, and Road Weather stopped updating in June 2026 | Milestone 4 |
| Eval | 12-14 fixtures planned | 14 tasks run in Claude Code 2.1.301: 14/14 pass; one guide error found (crime neighborhood placeholders are pre-2017) and fixed. See `evals/results/2026-10-15.md` | Milestone 4 eval run |
| Bundle | `mcpb pack .` | Allowlisted staging copy plus an archive check | Codex review of milestone 4 |
| Whole-project review | Per-branch reviews only | Kimi (Codex was rate-limited) plus Claude's publication checks: error-catalog examples made to match emitted envelopes (no `rate_limited` repair; no echoed value; cursor reasons per failure); unpopulated `coverage_warnings.assigned_fraction` and unused placeholder-share threshold removed; reference `detail` documented as accepted but ignored; describe duration corrected to about 25 s; stale docs fixed | Pre-publication review |
| Codex whole-project review | Kimi only | 7 findings, all fixed with regressions: upstream shape validation (no empty success from an error body), whole-call 30 s deadlines, budget check on empty pages, region-label failures surfaced (`labels_available`), describe samples sanitized, endpoint-aware error mapping (catalog 404 is `upstream_unavailable`; SoQL codes only from `/resource`), and an `instructions` head that names its sentinel. A per-tool fault matrix now asserts every emitted code is documented for its tool | Codex whole-project review |
| Context-vs-constraints review | Head rules only in `instructions`; capability `## Rules` mostly facts; rules mid-paragraph in guides | Head rules shared verbatim with the capability summary (facts moved to `## Semantics`); untrusted-text rule added to the head; area rule gains its `group_by` exception; "near-zero" replaced by `unreliable`/`partial` handling; guides gain a `## Rules` section; reference `chunk_id` and `detail` removed; `group_by`/`having` described; stability stated as `preview` | Claude review with the separating-context-from-constraints and agent-friendly-mcp skills |
| Clause-size cap | 4 KiB of raw clause text, said to bound the cursor at 6 KiB | 4 KiB measured as the compact UTF-8 JSON a cursor stores (escapes and list punctuation count); non-ASCII no longer escaped. Quote-heavy text and long lists are rejected sooner; cursors at the cap stay ≤ 6 KiB | Design audit (#11): quote-heavy and list inputs gave 6–11 KB cursors, and non-ASCII near the cap failed with `row_too_large`. Fixed in #12 |
| Advertised capabilities | FastMCP defaults: an empty `prompts` capability on both eras, `listChanged: true` on legacy | No `prompts` capability; `listChanged: false` on every era, set in `build_server` via FastMCP internals and pinned by a test | Design audit (#11); [9.list-changed]. Fixed in #12 |
| Value-echo disclosure | Catalog listed dataset ids only as catalog-minted | Catalog discloses that `dataset_not_found` and `dataset_not_queryable` echo the caller's pattern-constrained `dataset_id` | Copilot review of #11. Fixed in #12 |
| Release pipeline | PyPI behind a `PUBLISH_PYPI` variable; `.mcpb` built only at release; nothing ran the built artifacts | No variable: every `v*` tag publishes, gated by the `pypi` environment's required reviewer (as in cwms-tools). One shared `build.yml` builds the wheel, sdist, and `.mcpb` on every PR and release and smoke-tests them on Linux, macOS, and Windows. A manual run rehearses the pipeline and can upload to TestPyPI | Brian, 2026-10-16, before tagging v0.1.0 |
| Search discovery aids | Search returned only ranked candidates; an empty or off-topic page carried no hint, and a tabular search hid every federated asset | First-page tabular searches also look up up to 3 external assets concurrently; every query-bearing page gets `match_evidence` (visible word overlap, not a verdict); a `suggestion` when no result contains a query word; a head rule to treat results as candidates. No synonym map or out-of-scope list: the maintainer will not keep a vocabulary. Not a strong/weak label: a lexical check cannot certify relevance. Top-level fields, not new `DatasetSummary` fields, to stay additive | Critical review with Codex (2026-10-16): live probes for street trees, traffic collisions, and potholes returned off-topic tabular results while the federated asset existed; Brian approved the design the same day |
| Defect reporting | The tracker was named only in pyproject.toml, manifest.json, and the README, none of which an agent reads mid-failure | A head rule in `instructions` (restated in the capability summary) points at the tracker and says to offer it to the user rather than file unasked, since the tracker is public; the capability summary's `## Defects` separates server defects from upstream conditions and the publisher's data. A head rule, not a footer line, so the existing restatement test covers it | Brian, 2026-10-16, following the same change in cwms-tools (#156) and amicus |
| Coverage-warning scope | Clauses mode scanned `where` and `having` only; raw SoQL scanned the whole statement | Clauses mode scans every clause the agent wrote (`select`, `where`, `group_by`, `having`, `order_by`), so both modes agree; the `$select` sent for an omitted `select` is not scanned; string literals and `AS alias` names are not references (Codex review). The head rule names grouped counts | #15: a live `group_by neighborhood` on `tazs-3rd5` returned no warning (2026-10-16) |

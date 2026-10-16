# Seattle Open Data MCP — Design Plan

Status: implemented through milestone 4. This document keeps the design as planned; where the build diverged, §12 records the change and the body has been updated to match.
History:
- **v1:** Claude and Codex (two rounds via amicus).
- **v2:** revised after a critical review by an independent Opus 5.5 agent that ran live probes; its headline findings were re-run by Claude.
- **v2.1:** Codex round 3 fixed paging correctness, region-resolution rules, cursor bounds, repair intent, versioning, and merge losses.
- **v2.2:** a second Opus review found that computed-region columns exist on only 2 of 147 datasets and are mostly null where they do exist.
  - The `regions` resolver is replaced by "place fields" grounded in a cross-dataset probe.
  - The engine is slimmed down.

§12 records every change and its source.
Standard: `~/projects/skills/agent-friendly-mcp`, full checklist walk; rule ids are cited inline, and §10 is the Done-Criteria walk.
Name (decided 2026-10-15): `seattle-open-data`, tool prefix `seattle_data_`.

## 1. Landscape verdict

No existing server is very good.
- **Closest:** cyanheads/socrata-mcp-server. TypeScript, any domain, defaults to Seattle, strong error hints, but its tests don't run in CI and it publishes no outputSchema.
- **pipeworx-io/mcp-data-seattle:**
  - No schema exposure, and unfiltered catalog search (498 of 699 hits are un-queryable ArcGIS links).
  - No truncation signal, and it takes the app token as a tool argument.
  - Generated code, no tests.
- **Official:** Tyler/Socrata ships no MCP server.

Gaps nobody fills:
- Honest neighborhood and area filtering.
- Data-coverage honesty.
- Structured output and byte-budgeted results.
- Curated Seattle dataset knowledge.
- Handling of federated and derived catalog assets.

## 2. Upstream facts that drive the design

Labels on each fact:
- **Verified:** re-run live by Claude, 2026-10-15/16.
- **(review):** run by an Opus reviewer, not re-run by Claude.
- **(brief):** reported by the original research agent.

**Catalog**
- Verified: `domains=data.seattle.gov` returns 0 catalog results, while `domains=cos-data.seattle.gov` returns 699 entries, 147 of them `only=dataset`.
- (brief) Catalog types:

  | type | count |
  | --- | --- |
  | federated_href | 498 |
  | dataset | 147 |
  | href | 18 |
  | filter | 9 |
  | map | 9 |
  | chart | 7 |
  | file | 7 |
  | story | 4 |

- (review) `filter` views are queryable and apply their saved filter (`8tqq-u7ib` returns 2,124 rows vs its parent's 193,682), and return pretty-printed JSON.
- (review) `map` views return their parent's rows.
- (review) Catalog `link` fields use the `cos-data.seattle.gov` host.
- (review) A federated item's access point is a Hub HTML page, not a FeatureServer URL.

**Place fields: the basis for neighborhood and area questions**
- Verified: only 2 of the 147 native datasets have `:@computed_region_*` columns: Fire 911 `kzjm-xkqj` and 2018 Paid Parking Occupancy `6yaw-2m8q`. SPD Call Data has none.
- Verified: on Fire 911, the council-district layer `cyqu-gs94` is mostly null. Over all time it has district 1 = 107,991, district 2 = 57,522, district 3 = 1, null = 2,050,366; districts 4–7 never appear.
- (review) In September 2026 the MCPP, beat, and zip region layers were about 99% filled, against about 8% for the tract and council layers.
- (review) Region layers were last updated in 2015–16. The council layer likely predates the 2022 redistricting. `q256-3sug` holds national 2010 ZCTAs.
- Verified (milestone 2): region-layer label columns, sizes, and update dates:

  | layer | label column |
  | --- | --- |
  | ru88 (MCPP) | `name` |
  | cyqu (council) | `c_district`, a number; `_feature_id` ≠ district number |
  | kuhn (beats) | `name` |
  | q256 (zips) | `zcta5ce10` |
  | 2day (tracts) | `name10` |

- Verified: the flagship datasets carry text place fields that are fully populated as columns, but often hold placeholder values.

  | dataset | field | distinct | notes |
  | --- | --- | --- | --- |
  | SPD Call Data `33kz-ixgy` (11.1M rows) | `dispatch_neighborhood` | 61 | `-` 727,487; `UNKNOWN` 31,729; `NULL` (string) 2,371 |
  | SPD Call Data | `dispatch_precinct` | 6 | UPPERCASE; `UNKNOWN` 88,989 |
  | SPD Call Data | `dispatch_sector` | 19 | |
  | SPD Call Data | `dispatch_beat` | 158 | |
  | SPD Crime Data `tazs-3rd5` (1.57M rows) | `neighborhood` | 62 | **`-` 745,994 (about 48%)**, almost all before 2017: 98-100% placeholders 2008-2016, 1-4% since (verified by year after the eval run); also `UNKNOWN`, `OOJ`, `FK ERROR` |
  | SPD Crime Data | `precinct` | 7 | Title case (`East`); `-`, `OOJ` |
  | Use of Force `ppi5-g2bj` | `precinct` | 9 | `-`, `0`, `X`, `OOJ` |
  | Building Permits `76t5-zqzr` | `originalzip` | 46 | |
  | Fire 911 | none | — | Only computed regions |

  - No SPD dataset has a council-district field.
  - SPD neighborhood text uses the MCPP vocabulary, with drift from the `ru88` layer: `DOWNTOWN COMMERCIAL` vs the layer's `DOWNTOWN COMMERICAL`, and `CHINATOWN/INTERNATIONAL DISTRICT` vs `INTERNATIONAL DISTRICT - EAST/WEST`.
- Verified: on Fire 911, `ru88-fbhk` name like `%CAPITOL%` returns `_feature_id` 8 (CAPITOL HILL) and 44 (NORTH CAPITOL HILL). September 2026 with `('8','44')` returns `count` 556.

**Column statistics and latency**
- Verified: SPD Call Data has `cachedContents` on 0 of 48 columns.
- Verified: Fire 911 `type.cachedContents.cardinality` is 2,215,833 (the row count); live `count(distinct type)` is 271.
- (review) Cached `count` lags the live `count(*)`.
- Live aggregates on the 11M-row SPD dataset have variable latency.
  - (review) `count(*)` returned in 0.7–2.7 s.
  - Verified: during Claude's probe, one aggregate exceeded 60 s, two exceeded 120 s, and a later grouped count returned normally.
  - Verified: `count(distinct location1)` on Building Permits returned HTTP 500.

**Paging and response facts**
- (review) `$where=:id > '<id>'&$order=:id` matches OFFSET paging on all four flagship datasets and on a filter view.
  - `:id` order is not lexical, so ids are never compared client-side.
  - Default SODA2 order is not `:id`.
- (review) A raw `$query` with no LIMIT, or a `$select` with no `$limit`, returns 1,000 rows; `LIMIT 60000` returns 60,000.
- (review) Responses are chunked and can be closed early: `LIMIT 200000` is 72 MB uncompressed and took 12.5 s.
- (review) SODA2 includes `:@computed_region_*` columns by default and omits null keys per row. The `X-SODA2-Fields` and `X-SODA2-Types` headers carry column descriptors.
- (review) Upstream error codes form a `query.soql.*` family: `no-such-column` and `type-mismatch` include a position, while `query.soql.invalid` (seen on a filter view) has none.

**Time**
- (review) Fremont `65db-xm6k` has `rowsUpdatedAt` of Oct 14, but its newest `date` value is 2026-09-30T23:00.
  - Its `date` column is `calendar_date` in `/api/views` but `floating_timestamp` in `X-SODA2-Types`.
- (review) Fire 911's `max(datetime)` tracked Pacific local time. That is evidence for this one dataset, not a rule.

**Geography**
- (review) SPD `dispatch_latitude`/`dispatch_longitude` are `text` columns holding `"REDACTED"` (about 24% of rows), so `within_circle` cannot run on them.
- Verified: Building Permits `latitude`/`longitude` are `number` columns, and Fire 911 `report_location` is a `point`.
- (review) Building Permits `location1` supports `within_circle`.

**Query API and tokens**
- Verified: SODA3 queries currently succeed without a token (200), although the docs require one.
- (review) SODA3's `page` stacks on top of SQL LIMIT/OFFSET, and SODA3 injects system columns even with an explicit SELECT.
- (review/brief) An invalid `X-App-Token` returns 403 on SODA2 and SODA3.

**performance.seattle.gov (out of scope)**
- Verified: its catalog holds 983 entries, 932 of them datasets, and its homepage redirects to seattle.gov/innovation-and-performance.
- Verified: the most-viewed entries are narrative "story" pages (e.g. "Homelessness Response").
- Verified: its datasets are tiny KPI tables backing those pages. The median has 2 columns, and 95% have 5 or fewer.
- Verified: they are mostly stale; only 16 were updated after 2021.
- Same Socrata API, but fundamentally different content: dashboard summaries, not record-level operational data.

**Content**
- (review) Dataset descriptions are raw HTML with inline styles.
- (review) Rows contain publisher free text.

## 3. Scope: generic Socrata vs Seattle

**Direct answer: generalize the query semantics now, and ship Seattle-only.**

Querying, paging, error mapping, schema reading, and computed-region detection behave the same on every Socrata portal.
That code is written portal-agnostically from day one.

Do not build a `Portal` abstraction or a `profiles/` package from a single example.
- Seattle's facts live in one constants module:
  - public host, catalog domain alias, and host rewrite;
  - the place-field map: which columns are neighborhoods, precincts, and so on, plus known placeholder values and reliability flags;
  - region-layer label columns and vintages;
  - the guide index.
- The `Portal` type is extracted when a second portal arrives.

Why ship Seattle-only rather than a generic server:
- An optional `domain` that defaults to Seattle makes "Chicago crimes" silently query Seattle.
- A required `domain` adds a first-call failure point, compounded by catalog aliases like `cos-data`.
- Answer quality comes from curated per-portal knowledge. §2 shows how much it matters: placeholder values, the gaps in the council layer, and vocabulary drift.
- `seattle_data_*` names stay unambiguous next to other servers [1.name] [3.naming].

Path beyond Seattle, decided later:
- **Preferred:** a startup-configured single portal (`--portal ...`) with its own tool prefix.
- **Only if evals show cross-jurisdiction tasks matter:** a multi-portal deployment with a required `portal` argument.

A portal counts as supported only after its catalog identity, asset-type mix, place fields, and query behavior have been probed and a live smoke test passes.

## 4. Primitives

Tools carry everything an agent must be able to do.
Resources carry static reference material.
No prompts in v1 [5.optional].

### Tools (4)

All four tools share these properties:
- **Annotations:** `readOnlyHint: true` and `openWorldHint: true`, except `seattle_data_get_reference`, which has `openWorldHint: false`. No destructive or idempotent hints [3.annotation-defaults].
- **`detail`:** `"summary" | "full"`, default `summary`. It changes field density only, never row count [8.detail-row-count] [8.detail-orthogonal].
- **Schemas:** JSON Schema 2020-12, with `outputSchema` on all four [3.output-schema].
  - Input objects are closed.
  - **Output:** each tool's top-level result object is open (`additionalProperties: true`), documented as the forward-compatible extension point for minor-release additions. Nested output objects are closed [3.closed-schemas] [9.additive-direction].
  - Two nested dynamic maps are documented exceptions: query `rows[]` entries, whose keys follow the column descriptors, and `repair.arguments`.
- **Nulls:** source nulls in `rows` are always present.
  - SODA2 omits null keys, so the server fills in every selected column.
  - Null-stripping in summary mode applies only to metadata [8.strip-nulls].
- **`now_local`:** every result's `source` object carries the current Seattle wall-clock time, so the agent can work out "last month" before writing a filter against floating timestamps.
- **Continuation (the single statement of this rule):**
  - A call with `cursor` accepts only the cursor, `detail`, `page_size`, and (query only) the repeated `dataset_id`, which must match the cursor's.
  - Any other argument fails with `invalid_argument`.

1. **`seattle_data_search_datasets`** — find candidate datasets.
   - **Inputs:**
     - `query?`: omit to browse.
     - `asset_scope?: "tabular" | "all"`, default `tabular`.
     - `category?`
     - `page_size?` (1–50, default 10) and `cursor?`.
   - **Classification:**
     - `tabular` returns native datasets plus filter views, which are marked `derived_from: <parent_id>`.
     - Map and chart views are folded into their parent.
     - `all` adds federated and external links with `queryability: "external"`.
   - **Returns:** `dataset_id`, `title`, `asset_type`, `queryability` (`supported | external | unsupported | unknown`), `updated_at`, `portal_url` (rewritten to the `data.seattle.gov` host), and `guide_uri` when a curated guide exists.
   - Descriptions are converted from HTML to plain text (`full` only).

2. **`seattle_data_describe_dataset`** — decide whether and how to use a dataset.
   - **Inputs:**
     - `dataset_id` (required; pattern `^[a-z0-9]{4}-[a-z0-9]{4}$`).
     - `column_query?`: omit for all columns.
     - `sample_rows?` (0–5, default 0).
   - Not paged: the largest flagship dataset has 48 columns, so summary output (name and type) is small.
   - **Returns:**
     - **`row_count`:** live `count(*)` with a 10 s deadline.
       - On timeout it falls back to the cached count, labeled `origin: "cached_metadata"`, or to `null` with `row_count_status: "timeout"`.
     - **`data_coverage`:** `{column, min, max, origin}` for the guide's primary time column(s), or else the first time-typed column.
       - Both `floating_timestamp` and `calendar_date` count as time-typed.
       - It has the same 10 s deadline and fallback.
     - **`columns`:** API name and type in summary.
       - `full` adds the plain-text description and `top`/`smallest`/`largest`/`null` from `cachedContents`, labeled `origin: "cached_metadata"`.
       - `stats_available: false` appears when the dataset has none, and `cardinality` is never exposed.
     - **`place_fields`:** columns an agent can use for "in <area>" questions.
       - Summary per field: `{column, kind, source}`.
         - `kind` is one of `neighborhood | precinct | sector | beat | zip | council_district | census_tract`.
         - `source` is `"column"` or `"computed_region"`.
       - Text columns are listed only when the Seattle constants module knows them. Computed-region columns are detected structurally on any portal.
       - **`reliability`** (`ok | partial | unreliable`) with a reason, from the constants module.
         - Example: Fire 911's council-district layer is `unreliable`, because it covers only districts 1–3 and is about 92% null.
         - Example: SPD Crime `neighborhood` is `partial`, because about 48% of rows are `-`.
       - `full` adds `values[]` (≤100 values).
         - For computed-region fields, each entry is `{label, value}`, where `value` is the `_feature_id` to use in `where`.
         - `full` also adds `placeholder_values[]` with row counts, `assigned_fraction`, and (for computed regions) `layer_id`, `label_column`, and `boundary_vintage`.
         - These come from one grouped query per field under a 10 s deadline. A field whose stats time out reports `stats_status: "timeout"`.
     - **`geo_columns`:** columns usable with `near`. Text-typed lat/lon columns appear under `geo_unusable` with the reason.
     - **`time_semantics`:** per time column, with the evidence behind any timezone claim.
     - `queryability`, `guide_uri`, and verified example queries from the guide.

3. **`seattle_data_query_dataset`** — bounded read or aggregate.
   - `dataset_id` (required).
   - **Modes:** `mode?: "clauses" | "soql"`, default `clauses`. The two modes are exclusive.
     - **Clauses mode:**
       - Arguments: `select[]`, `where`, `group_by[]`, `having`, `order_by[]`, `search_text`, `near`.
       - Each omitted clause is not applied, and empty arrays or strings are rejected.
       - Omitting `select` sends an explicit `$select` of the published data columns from the cached schema, so system and computed-region columns stay out.
     - **SoQL mode:** requires `soql`, rejects every clause argument, and runs the query verbatim.
   - **Area filters** are ordinary `where` predicates on place fields.
     - Text field example: `dispatch_neighborhood = 'CAPITOL HILL'`.
     - Computed-region example: `:@computed_region_ru88_fbhk IN ('8','44')`, using `values` from describe.
     - No server-side name resolver in v1 (§12).
   - **`coverage_warnings[]`:** emitted when `where` (or `having`, or raw `soql`) references a place field whose reliability is `partial` or `unreliable`.
     - Fields are matched by identifier against the cached schema.
     - Each warning carries `{column, reliability, reason}`.
     - This is the guard against the silent false zero (fixtures 13–14).
   - **`near?`:** `{column, latitude, longitude, radius_m}`, compiled to `within_circle`.
     - The column is validated against `geo_columns`.
     - A text-typed or redacted column fails with `column_not_geographic`.
       - The primary repair is inspection: `seattle_data_describe_dataset` with `detail: "full"`.
       - The `alternative` names the dataset's place fields as a non-equivalent substitute [6.repair-intent].
   - **Paging:** `page_size?` (1–500 rows, default 100) and `cursor?` (§5).
   - **Returns:**
     - Column descriptors, `rows[]`, `returned_rows`, `has_more`, `next_cursor`, `truncated`, `truncation_reason`, `consistency`, and `coverage_warnings`.
     - In `full`: `query_url`, a reproducible SODA2 URL, and the effective query.
     - In `full`, clauses mode only: **`download_url`**, the same query as a SODA2 `.csv` URL with `$limit` set to `row_count`.
       - The server builds this URL but never fetches it.
       - This is the answer to "I need every row" without server-side bulk export, and it keeps bulk data out of the agent's context.
       - It is omitted, with `download_url_omitted: true`, when longer than 2 KiB.

4. **`seattle_data_get_reference`** — capabilities, guides, SoQL help, and the error catalog.
   - **Inputs:**
     - `topic?: "index" | "capabilities" | "guides" | "soql" | "errors"`, default `index`.
     - `item_id?`: requires a topic other than `index`.
     - `chunk_id?`: requires `item_id`.
   - An invalid combination fails with `invalid_argument`, and the repair returns that topic's index.
   - With no arguments it returns the index. It is the tool fallback for every resource [4.tool-fallback].

Search → describe → query is a justified split: agents branch on candidates and inspect the schema between steps (design-workflow Step 3).
A known dataset id and known fields need no preliminary call.

### Resources (static, process-versioned)

- `seattle-data://capabilities`
- `seattle-data://guides` and `seattle-data://guides/{guide_id}` (guides have one density, so no `detail`)
- `seattle-data://soql/{section_id}`
- `seattle-data://errors/{section_id}`

Index entries carry title, description, size, and version [2.index-not-bodies].
Long guides chunk under stable URIs [4.chunk-fetch-path].
Cache hints: resource reads, the native lists, and `server/discover` use `cacheScope: "public"` and `ttlMs: 3600000` [8.cacheable-results].
Template completion is deferred: the guide and section ids are few and listed in the index.
v1 has no live dataset resource (§12).

### Curated guides (v1 set)

Each guide covers:
- the unit of observation;
- key fields;
- the primary time column and its timezone evidence;
- place fields with their reliability;
- verified queries.

Dataset-specific pitfalls from §2:

- **Fire 911 (`kzjm-xkqj`):**
  - Most calls are medical (Aid Response, Medic Response), not fires.
  - The only area filters are computed regions. MCPP, beat, and zip are usable; council district and census tract are unreliable.
- **SPD Call Data (`33kz-ixgy`):**
  - It counts calls, not crimes.
  - It has no council-district field; use `dispatch_precinct`, `dispatch_neighborhood`, or `dispatch_beat`.
  - `dispatch_neighborhood` placeholders: `-`, `UNKNOWN`, `NULL`.
  - Coordinates are text and about 24% redacted, so `near` is unavailable.
- **SPD Crime Data (`tazs-3rd5`):**
  - It counts reported offenses.
  - About 48% of `neighborhood` values are `-`, so neighborhood counts are severe undercounts; prefer `precinct`, `sector`, or `beat`.
  - Precinct is Title case (`East`), unlike SPD Call Data (`EAST`); compare with `upper()`.
- **Building Permits (`76t5-zqzr`):** `location1` is the geo column, and `originalzip` is the area field.
- **Fremont Bridge (`65db-xm6k`):**
  - Counts are hourly and combine bikes and scooters.
  - Data lags `updated_at` by weeks.
  - Its `date` type differs between metadata and responses.
- **Use of Force (`ppi5-g2bj`):**
  - Precinct placeholders: `-`, `0`, `X`, `OOJ`.
  - The unit of observation is to be verified in milestone 3.
- **Neighborhood vocabulary note** (in the SoQL guide): SPD text neighborhoods and the `ru88` layer share MCPP names but drift (§2).

## 5. Query engine

**Transport: SODA2 GET only in v1.**
- An optional `SOCRATA_APP_TOKEN` is sent as `X-App-Token`.
- The token is never accepted as a tool argument, never echoed, and never recorded in test fixtures.
- A rejected configured token is an operator error; the server never silently falls back to anonymous access.
- A generic 403 maps to `upstream_forbidden` unless the response body proves the token was the cause.
- SODA3 is evidence-gated (§9).

**Response handling**
- Responses are read with an incremental JSON parser such as ijson, never line by line: filter views pretty-print (§2).
- The server stops reading as soon as the page is full, then closes the connection.
- Generated queries send an explicit `$select` built from a per-process cached schema (TTL 1 h, refreshed on `no_such_column`).
  - Missing keys are filled with null from that schema.
- Raw SoQL column descriptors come from `X-SODA2-Fields`/`X-SODA2-Types`, so no metadata call is needed.

**Paging by query kind.** Only clauses-mode queries are paged.

| Query kind | Ordering | Continuation | `consistency` |
| --- | --- | --- | --- |
| Row query, no `order_by` | `:id` ascending | keyset: `:id > last_emitted_id` | `stable` |
| Row query with `order_by`, grouped, aggregate, or `DISTINCT` | Caller's ordering plus tie-breakers: `:id` for row queries; all group keys or selected columns otherwise | OFFSET = cumulative emitted count | `best_effort`. [8.cursor-pagination] permits OFFSET for stable ordering over small sets, and the 10k cap bounds them |
| Raw SoQL | Verbatim | **Not paged** | n/a |

- **Raw SoQL:**
  - The server stops after `page_size` rows or the byte budget.
  - When the upstream had more rows, the response sets `truncated: true` and `truncation_reason: "raw_soql_not_paged"`.
    - The hint reads: set `LIMIT` ≤ `page_size` and advance `OFFSET`, or switch to clauses mode.
  - A raw query without a LIMIT gets SODA2's 1,000-row default.
- **Cursor:**
  - HMAC-signed with a 32-byte random key held **in process memory**.
    - A restart invalidates all cursors, which then fail with `cursor_expired`. That is the same recovery path as expiry.
  - Contents:
    - cursor format version;
    - `dataset_id`;
    - the normalized clauses query and ordering;
    - the cumulative `emitted_count`;
    - `last_emitted_id` (keyset only);
    - `issued_at`.
  - Cursors expire after 1 hour.
  - A tampered or unparseable cursor fails with `invalid_cursor`.
  - The cursor advances only past rows actually emitted, and changing `page_size` never resets `emitted_count`.
  - Query text inside a cursor is never echoed in errors [3.sec-handle-modes] [8.cursor-lifetime].
- **Logical cap:** a paged query emits at most 10,000 rows in total.
  - The `page_size + 1` fetch that reaches the cap shows whether more rows exist.
  - If they do, the final page has `truncated: true`, `has_more: false`, and no `next_cursor`, plus an aggregation repair.
  - Exactly 10,000 rows gives `truncated: false`.
- **Terminal combinations:**
  - `has_more: true` ⇔ `next_cursor` is present.
  - `truncated: true` implies `has_more: false`.
- **Byte budget:** internal, not a caller knob.
  - The final serialized result, including the compatibility `content`, stays at or under 24 KiB.
  - Envelope overhead is measured first, then whole rows are packed.
  - A single row that cannot fit fails with `row_too_large`, with projection guidance [8.truncation].
- **Size bounds:**
  - Clauses-query text is capped at 4 KiB, which bounds the encoded cursor at 6 KiB or less.
  - `query_url` is omitted, with `query_url_omitted: true`, when longer than 2 KiB.
- A caller's own LIMIT is query semantics, not truncation.
- **Deadlines:**
  - 30 s per upstream query request, and 10 s for each describe aggregate (§4). The query deadline is configurable.
  - Responses are capped at 8 MiB decoded.
  - A timed-out query returns `query_timeout`, never a partial success.

**Values**
- Numbers stay exact decimal strings.
- `floating_timestamp` and `calendar_date` values stay local datetimes with no `Z` suffix.
- Server timestamps are RFC3339 UTC [8.locale-independent].

**Untrusted content** [3.sec-untrusted-content] [3.sec-sanitize]
- Catalog and description HTML is converted to text.
- Control characters are stripped from row values.
- Capabilities state that dataset text is publisher data, not instructions.

## 6. Error taxonomy

One envelope, two carriers [6.rename]:
- **Tools:** `structuredContent` with `isError: true`.
- **Resources:** JSON-RPC `error.data`, with `machine_code`/`human_message`.

Every error carries `temporary`, a nullable `retry_after_ms`, and `request_id` [6.correlation].
`repair` is a single object `{next_step, tool, arguments, alternative?}` naming a real tool with literal arguments [6.repair-object] [6.repair-intent] [6.repair-callable].
Framework argument-validation failures are normalized into this envelope, and a forced-error wire test checks it.

| code | temporary | repair (`next_step`: tool + arguments) |
| --- | --- | --- |
| `no_such_column` | false | `inspect_columns`: `seattle_data_describe_dataset({dataset_id, column_query: <the unknown name>})`; candidates go in `details` |
| `column_not_geographic` | false | `inspect_geography`: `seattle_data_describe_dataset({dataset_id, detail: "full"})`; `alternative` names the place fields as a non-equivalent substitute |
| `soql_syntax_error` (every upstream `query.soql.*` code; position optional) | false | `read_syntax`: `seattle_data_get_reference({topic: "soql", item_id: "syntax"})`; upstream code and position go in `details` |
| `dataset_not_found` | false | `browse`: `seattle_data_search_datasets({})`; `alternative` says the id may belong to another portal |
| `dataset_not_queryable` | false | `find_tabular`: `seattle_data_search_datasets({asset_scope: "tabular"})`; `alternative` gives the external link |
| `app_token_rejected` | false | `read_auth`: `seattle_data_get_reference({topic: "errors", item_id: "authentication"})` (operator action) |
| `upstream_forbidden` | false | none |
| `rate_limited` | true | none: `temporary: true` already means retry unchanged after `retry_after_ms` (from Retry-After); a literal retry repair would echo caller text |
| `query_timeout` | true | `narrow`: the same tool and `dataset_id`; `alternative` suggests aggregation or a tighter `where` |
| `cursor_expired`, `invalid_cursor` | false | `restart_from_first_page`: `seattle_data_query_dataset({dataset_id})`; `alternative` says to resend the original clauses |
| `reference_not_found` | false | `list_items`: `seattle_data_get_reference({topic})`; `details.candidates` lists valid ids. Also the resource not-found code (JSON-RPC -32602) |
| `internal_error` | false | none; logged with `request_id` |
| `row_too_large`, `invalid_argument`, `upstream_unavailable` | per code | per catalog |

Free-text offending values (`where`, `soql`) are omitted from `details.value`, and the error catalog discloses that policy [6.offending-value].
Separately, cwms-tools has two gaps Codex found:
- Validation errors bypass its envelope (`mcp/tools.py:14`).
- Its error schema does not require `temporary`/`retry_after_ms` (`output_schema.py:89`).

## 7. Execution model

All calls are blocking `tools/call` [7.execution-mode].
- **Expected latency** [7.declare-duration]:
  - 1–3 s for metadata;
  - up to 30 s for describe and for query, each bounded as a whole call (metadata fetch included), then `query_timeout`. httpx timeouts alone are per read, so a trickling response needed the overall deadline;
  - up to 30 s for queries, then `query_timeout`.

  The latency is stated in the tool descriptions.
- **Cancellation:** over stdio, `notifications/cancelled` aborts the upstream request [7.cancellation].
- **Progress:** no progress notifications in v1.
- **Tasks extension:** N/A. No operation outlives a request.

## 8. Discovery surface

- **Protocol target:** MCP 2026-07-28, compatible with 2025-11-25, with no required extensions [1.spec-revision].
- **Transport:** stdio only in v1 [1.transport].
- **Negative scope** comes first in the capability summary and `instructions`:
  - No writes or private datasets.
  - No other portals, including performance.seattle.gov.
  - No ArcGIS feature queries: federated assets are discoverable only, with their external link.
  - No address geocoding or forecasts.
  - No server-side bulk export.
    - A complete row set is reachable three ways: paged clauses queries split by `where` (e.g. by time window), raw SoQL `LIMIT`/`OFFSET` walks, or the `download_url`.
    - Questions about all rows are best answered with server-side aggregation.
- **`instructions`:** ends with the sentinel `END SEATTLE OPEN DATA INSTRUCTIONS` [2.instructions-prefix] [2.truncation-signal].
  - Only branch rules go in the head:
    - for "in <area>" questions, use describe's `place_fields` and heed `coverage_warnings`;
    - check `data_coverage` before "latest" claims;
    - use `now_local` for relative dates.
- **`tools/list` byte budget:** 24 KiB, enforced in CI [2.compact-baseline].
- **Versioning:**
  - SemVer with a CHANGELOG.
  - **Deprecation window:** at least 90 days or two minor releases, whichever is longer. Removal only at a major version after 1.0 [9.deprecation-semantics].
  - **Output additions:**
    - New top-level output fields ship in minor releases; the top-level result objects are open for exactly this (§4).
    - New nested fields are breaking: a minor bump before 1.0, a major bump after.
    - Rediscovery is signalled by the version in `server/discover` and bounded by the 1-hour `ttlMs` [9.additive-direction].
  - `listChanged` is not advertised: the surface never changes within a process [9.list-changed].

## 9. Stack and milestones

**Stack:** Python, uv, FastMCP 4, Pydantic, httpx, ijson; ruff, ty, prek.
- Mirror cwms-tools' shared `core/` producers with a thin `mcp/` layer.
- The Typer CLI and its parity tests come after release.
- Tests: recorded HTTP fixtures (`tests/http_fixtures.py`) plus an opt-in live `integration` marker; no volatile exact counts in assertions.

**Milestones**

1. **Contract spike.** Exit tests:
   - (a) A snapshot test of all four tool schemas.
   - (b) A forced-error wire test per tool, covering framework validation failures and a `query.soql.*` mapping.
   - (c) A measured `tools/list` byte count checked against 24 KiB in CI.
   - (d) One `host_capture` each from Claude Code and Codex: the tool list as received, plus one real call.
   - (e) A property test that every emitted `repair` validates against the schema of the tool it names.
2. **Discovery and inspection.**
   - Catalog alias, asset classification, and the host rewrite.
   - `describe`: `row_count` and `data_coverage` with deadlines and fallbacks; handling of missing `cachedContents`; `place_fields` from the constants module plus structural computed-region detection.
3. **Query core.**
   - Clauses and SoQL modes, `near`, and `coverage_warnings`.
   - Stateless cursors, the byte budget, the incremental parser, null fill-in, and cancellation.
4. **Release.**
   - The seven curated guides.
   - MCPB bundle.
   - Eval run with transcript review.
5. **Later, each gated on evidence:**
   - A Typer CLI with parity tests.
   - SODA3 (if SODA2 is throttled or enforced).
   - A startup-configured second portal.
   - ArcGIS federated reads.
   - A live dataset resource.
   - A server-side place-name resolver (if evals show agents mistype values).
   - Template completion.
   - Describe progress notifications.
   - A capability fingerprint and lifecycle `_meta` [9.fingerprint].

### Eval fixtures (design-workflow Step 8)

1. **Cold start:** find Fire 911 with other data servers installed.
2. **Disambiguation:** SPD calls vs SPD crime.
3. **Fire calls in Capitol Hill last month:**
   - describe in `full` gives the MCPP `values`;
   - the agent uses `now_local`;
   - it makes the CAPITOL vs NORTH CAPITOL choice explicitly;
   - it gives the medical-vs-fire caveat.
4. **SPD calls in Council District 3 this year:** the agent says SPD data has no council-district field and offers precinct or neighborhood instead.
5. **Fremont "this month":** the agent reports the coverage lag instead of a false zero.
6. **Building permits:** within 500 m of a coordinate.
7. **SPD calls near a coordinate:** `column_not_geographic`, then describe, then a `dispatch_neighborhood` alternative stated as non-equivalent.
8. **Council-district boundaries:** a federated result yields the external link, with no retry loop.
9. **DST edge:** a floating-time value at a DST transition.
10. **Repair:** a wrong column, then malformed SoQL.
11. **Pagination:**
    - keyset continuation;
    - OFFSET continuation after a byte-limited short page;
    - the 10,000 vs 10,001 boundary;
    - `raw_soql_not_paged`;
    - tampered, expired, and restarted-server cursors.
12. **Tokens:** a rejected token vs a generic 403.
13. **Fire calls in Council District 3:** a `coverage_warnings` entry marks the layer unreliable, and the agent does not report a near-zero count as fact.
14. **SPD crimes in Capitol Hill:** the agent surfaces the roughly 48% unassigned-neighborhood caveat, or switches to precinct or beat.

**Host captures:** one per target client, plus a tools-only (no resources) host run.
**Metrics:**
- First-call correctness ≥95% and first-repair correctness ≥90%.
- Zero silent completeness, coverage, or timezone errors.
- Tokens, calls, and bytes per task.

## 10. Done-Criteria walk (§1–§9)

- **Answered:** the design commits to it.
- **Pending:** a milestone must produce evidence first.

| § | Status | Answer |
| --- | --- | --- |
| 1 Server | answered | Name and prefix (§3). stdio, logs to stderr. Auth: an optional operator token with three distinct failure modes (none / rejected / generic 403). Implicit state: the cached schemas and an in-memory signing key, both lost on restart and documented. State handles: signed cursors with lifetime, binding, and expiry (§5) |
| 2 Discovery | answered (M1) | Capability resource plus the reference tool; negative scope first. Measured `tools/list`: 13,252 bytes against the 24 KiB budget, enforced in CI. Host captures (docs/host-captures): Codex prefixes `instructions` to every tool description; neither host truncated it. No prefix length is measured, so no prefix claim is made [2.instructions-prefix] |
| 3 Tools | answered (M1) | Schemas (§4) with the documented open top level and dynamic maps; honest read-only annotations; untrusted-content handling. Idempotency keys, version preconditions, and patch ops are N/A (read-only). Schemas frozen by `tests/snapshots/tools_list.json` |
| 4 Resources | answered | Static reference resources with templates, the tool fallback, and a 1-hour `ttlMs`. Completion deferred (few, listed ids). Subscriptions N/A (content changes only on upgrade) |
| 5 Prompts | answered | None in v1 |
| 6 Failure | answered (M1) | Envelope and taxonomy (§6) with literal repairs; per-tool code catalog and a worked payload per code at `seattle-data://errors/codes` [6.document-codes]; forced-error wire tests per tool, including framework validation failures |
| 7 Long-running | answered | §7; tasks N/A |
| 8 Token efficiency | answered (M3) | Summary/full, whole-row byte budget (wire result ≤ 24 KiB, tested), paging by query kind, 10,000-row cap with exact-boundary tests, HTML stripped, system columns hidden |
| 9 Versioning | answered | Deterministic ordering; deprecation window; open top-level output for additions (§8); `listChanged` not advertised; fingerprint deferred per [9.fingerprint] |
| Data facts | answered | Verified: catalog alias, computed-region coverage, council-layer nulls, place-field vocabularies and placeholders, `cachedContents` gaps; in M2 also layer label columns and dates, `within_circle` on a `point` column (121,985 Fire calls within 500 m of downtown), Fire 911 `datetime` as Pacific wall-clock time, and catalog `parent_fxf`/`access_points`. In M3 also: `:id` keyset paging equals OFFSET paging (two datasets), raw SoQL without LIMIT returns 1,000 rows, `$query` plus `$limit` is rejected (400), a 200,000-row response can be closed after 10 rows (3.2 s), and SODA2 accepts `$limit=1000000000` for `download_url` |

## 11. Product decisions (Brian, 2026-10-15)

1. **ArcGIS federated assets:** discovery only in v1, returned with their external link.
2. **performance.seattle.gov:** out of scope. It is a KPI-dashboard portal, not record-level data (§2).
3. **Bulk export:** not in v1, provided an agent can still get all the data it needs. It can, via split paged queries, raw SoQL `LIMIT`/`OFFSET`, and the never-fetched `download_url` (§4, §8).
4. **Transport:** stdio only.
5. **Name:** `seattle-open-data`, tool prefix `seattle_data_`.
6. **Region-layer vintage:** old or sparse layers stay visible, flagged `unreliable`, and are never used in guide examples (§4).

No open product questions remain. Engineering unknowns are tracked as **pending** in §10.

## 12. Decision log

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
| Done-Criteria gaps | §7, untrusted content, `request_id`, N/A lines missing | Added | Opus review 1 |
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

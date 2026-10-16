# Upstream facts

Probed facts about data.seattle.gov that the design depends on. `tests/test_live.py` re-checks the stable ones (`uv run pytest --run-integration -m integration`).

Labels on each fact:
- **Verified:** run live by Claude, 2026-10-15/16 unless a milestone is named.
- **(review):** run by an Opus reviewer, not re-run by Claude.
- **(brief):** reported by the original research agent.

Never add a fact you did not probe; label reviewer-reported facts as such.

## Catalog

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
- Verified (milestone 2): catalog entries carry `parent_fxf` (for derived views) and `access_points` (for federated entries); the server uses the ArcGIS access point as `external_url`.

## Place fields: the basis for neighborhood and area questions

- Verified: only 2 of the 147 native datasets have `:@computed_region_*` columns: Fire 911 `kzjm-xkqj` and 2018 Paid Parking Occupancy `6yaw-2m8q`. SPD Call Data has none.
- Verified: on Fire 911, the council-district layer `cyqu-gs94` is mostly null. Over all time it has district 1 = 107,991, district 2 = 57,522, district 3 = 1, null = 2,050,366; districts 4–7 never appear.
- (review) In September 2026 the MCPP, beat, and zip region layers were about 99% filled, against about 8% for the tract and council layers.
- (review) Region layers were last updated in 2015–16. The council layer likely predates the 2022 redistricting. `q256-3sug` holds national 2010 ZCTAs.
- Verified (milestone 2): region-layer label columns:

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

## Units of observation

- Verified (milestone 4): SPD Call Data rows are dispatches, SPD Crime Data rows are offenses, and Use of Force rows are subjects.
- Verified (milestone 4): Road Weather stopped updating in June 2026.

## Column statistics and latency

- Verified: SPD Call Data has `cachedContents` on 0 of 48 columns.
- Verified: Fire 911 `type.cachedContents.cardinality` is 2,215,833 (the row count); live `count(distinct type)` is 271.
- (review) Cached `count` lags the live `count(*)`.
- Live aggregates on the 11M-row SPD dataset have variable latency.
  - (review) `count(*)` returned in 0.7–2.7 s.
  - Verified: during one probe, one aggregate exceeded 60 s, two exceeded 120 s, and a later grouped count returned normally.
  - Verified: `count(distinct location1)` on Building Permits returned HTTP 500.

## Paging and responses

- (review) `$where=:id > '<id>'&$order=:id` matches OFFSET paging on all four flagship datasets and on a filter view. Verified (milestone 3) on two datasets.
  - `:id` order is not lexical, so ids are never compared client-side.
  - Default SODA2 order is not `:id`.
- (review) A raw `$query` with no LIMIT, or a `$select` with no `$limit`, returns 1,000 rows; `LIMIT 60000` returns 60,000. Verified (milestone 3) for raw SoQL without LIMIT.
- Verified (milestone 3): `$query` combined with `$limit` is rejected with HTTP 400.
- (review) Responses are chunked and can be closed early: `LIMIT 200000` is 72 MB uncompressed and took 12.5 s. Verified (milestone 3): a 200,000-row response can be closed after 10 rows (3.2 s).
- Verified (milestone 3): SODA2 accepts `$limit=1000000000`, which `download_url` relies on.
- (review) SODA2 includes `:@computed_region_*` columns by default and omits null keys per row. The `X-SODA2-Fields` and `X-SODA2-Types` headers carry column descriptors.
- (review) Upstream error codes form a `query.soql.*` family: `no-such-column` and `type-mismatch` include a position, while `query.soql.invalid` (seen on a filter view) has none.

## Time

- (review) Fremont `65db-xm6k` has `rowsUpdatedAt` of Oct 14, but its newest `date` value is 2026-09-30T23:00.
  - Its `date` column is `calendar_date` in `/api/views` but `floating_timestamp` in `X-SODA2-Types`.
- (review) Fire 911's `max(datetime)` tracked Pacific local time. Verified (milestone 2): Fire 911 `datetime` is Pacific wall-clock time. That is evidence for this one dataset, not a rule.

## Geography

- (review) SPD `dispatch_latitude`/`dispatch_longitude` are `text` columns holding `"REDACTED"` (about 24% of rows), so `within_circle` cannot run on them.
- Verified: Building Permits `latitude`/`longitude` are `number` columns, and Fire 911 `report_location` is a `point`.
- Verified (milestone 2): `within_circle` runs on a `point` column (121,985 Fire calls within 500 m of downtown).
- (review) Building Permits `location1` supports `within_circle`.

## Query API and tokens

- Verified: SODA3 queries currently succeed without a token (200), although the docs require one.
- (review) SODA3's `page` stacks on top of SQL LIMIT/OFFSET, and SODA3 injects system columns even with an explicit SELECT.
- (review/brief) An invalid `X-App-Token` returns 403 on SODA2 and SODA3.

## Content

- (review) Dataset descriptions are raw HTML with inline styles.
- (review) Rows contain publisher free text.

## performance.seattle.gov (out of scope)

- Verified: its catalog holds 983 entries, 932 of them datasets, and its homepage redirects to seattle.gov/innovation-and-performance.
- Verified: the most-viewed entries are narrative "story" pages (e.g. "Homelessness Response").
- Verified: its datasets are tiny KPI tables backing those pages. The median has 2 columns, and 95% have 5 or fewer.
- Verified: they are mostly stale; only 16 were updated after 2021.
- Same Socrata API, but fundamentally different content: dashboard summaries, not record-level operational data.

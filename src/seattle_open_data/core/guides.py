"""Curated dataset guides (docs/design.md: Primitives). Facts were probed live on 2026-10-15; every example runs in
`tests/test_live.py::test_guide_examples_run`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

SEPT_2026 = "'2026-09-01T00:00:00' and '2026-09-30T23:59:59'"


@dataclass(frozen=True)
class Example:
    question: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Guide:
    guide_id: str
    dataset_id: str
    title: str
    description: str
    body: str
    examples: tuple[Example, ...]
    #: Binding rules for this dataset, rendered ahead of the facts in `body`.
    rules: tuple[str, ...] = ()

    def render(self) -> str:
        examples = "\n".join(
            f"- {e.question}\n  ```json\n  {json.dumps(e.arguments)}\n  ```" for e in self.examples
        )
        rules = "".join(f"- {rule}\n" for rule in self.rules)
        rules = f"## Rules\n{rules}\n" if rules else ""
        return (
            f"# {self.title} (`{self.dataset_id}`)\n\n{rules}{self.body.strip()}\n\n"
            f"## Example queries\n\n{examples}\n"
        )


GUIDES: tuple[Guide, ...] = (
    Guide(
        "fire-911",
        "kzjm-xkqj",
        "Seattle Real Time Fire 911 Calls",
        "Seattle Fire Department dispatches, updated every 5 minutes; most are medical, not fires.",
        """
## What a row is
One Seattle Fire Department dispatch (incident). In September 2026, 9,601 rows had 9,601 distinct `incident_number` values, so `count(*)` counts incidents.

## Key fields
- `type`: response type. Most calls are medical: Aid Response and Medic Response together are about two thirds of all rows. "Fire calls" in ordinary speech usually means this whole dataset.
- `datetime`: dispatch time, Pacific wall-clock (verified against the live clock), no timezone suffix.
- `report_location` (point): use with `near`. `latitude`/`longitude` are plain numbers.

## Area filters
No text neighborhood field. Use the computed-region columns from `seattle_data_describe_dataset` with `detail: "full"`:
- `:@computed_region_ru88_fbhk`: SPD MCPP neighborhoods (about 99.6% assigned). CAPITOL HILL is `8`, NORTH CAPITOL HILL is `44`.
- `:@computed_region_q256_3sug`: ZIP code areas (2010).
- `:@computed_region_cyqu_gs94` (council districts) and `:@computed_region_2day_rhn5` (census tracts) are about 92% null and predate 2022 boundaries.
""",
        (
            Example(
                "How many calls of each type in September 2026?",
                {
                    "dataset_id": "kzjm-xkqj",
                    "select": ["type", "count(*) AS calls"],
                    "where": f"datetime between {SEPT_2026}",
                    "group_by": ["type"],
                    "order_by": ["calls DESC"],
                    "page_size": 10,
                },
            ),
            Example(
                "How many calls in Capitol Hill (MCPP 8) in September 2026?",
                {
                    "dataset_id": "kzjm-xkqj",
                    "select": ["count(*) AS calls"],
                    "where": f"datetime between {SEPT_2026} AND :@computed_region_ru88_fbhk = '8'",
                },
            ),
        ),
        rules=(
            'When a "fire calls" count spans all response types, say it covers every dispatch, most of them medical.',
            "Never use `:@computed_region_cyqu_gs94` or `:@computed_region_2day_rhn5` for counts.",
        ),
    ),
    Guide(
        "spd-calls",
        "33kz-ixgy",
        "SPD Call Data",
        "Seattle Police calls for service; rows are dispatches, so count distinct cad_event_number.",
        """
## What a row is
One dispatch of one unit to a call, not one call. In September 2026 there were 52,009 rows but 29,708 distinct `cad_event_number` values; `count(*)` overstates calls by about 75%.

## Key fields
- `cad_event_number`: the call.
- `initial_call_type`, `final_call_type`, `call_type`, `priority`.
- `cad_event_original_time_queued`: when the call was queued (Pacific wall-clock, no timezone suffix).
- About 11 million rows.

## Area filters
- `dispatch_precinct` (UPPERCASE: EAST, NORTH, SOUTH, SOUTHWEST, WEST), `dispatch_sector`, `dispatch_beat`, `dispatch_neighborhood` (MCPP names, e.g. CAPITOL HILL).
- `dispatch_neighborhood` uses placeholders `-`, `UNKNOWN`, and the string `NULL` for about 7% of rows.
- There is no council-district field.
- `dispatch_latitude`/`dispatch_longitude` are text and partly `REDACTED`: `near` cannot be used.

## Not crimes
These are calls for service, not reported offenses.
""",
        (
            Example(
                "How many calls did each precinct handle in September 2026?",
                {
                    "dataset_id": "33kz-ixgy",
                    "select": ["dispatch_precinct", "count(distinct cad_event_number) AS calls"],
                    "where": f"cad_event_original_time_queued between {SEPT_2026}",
                    "group_by": ["dispatch_precinct"],
                    "order_by": ["calls DESC"],
                },
            ),
            Example(
                "What were the most common final call types in Capitol Hill in September 2026?",
                {
                    "dataset_id": "33kz-ixgy",
                    "select": ["final_call_type", "count(distinct cad_event_number) AS calls"],
                    "where": f"dispatch_neighborhood = 'CAPITOL HILL' AND cad_event_original_time_queued between {SEPT_2026}",
                    "group_by": ["final_call_type"],
                    "order_by": ["calls DESC"],
                    "page_size": 10,
                },
            ),
        ),
        rules=(
            "Count calls with `count(distinct cad_event_number)`.",
            "Always filter on time.",
            "Aggregate rows rather than listing them.",
            "For reported offenses, use SPD Crime Data (`tazs-3rd5`) instead.",
        ),
    ),
    Guide(
        "spd-crime",
        "tazs-3rd5",
        "SPD Crime Data: 2008-Present",
        "Reported offenses (NIBRS); one report can hold several offenses; neighborhood is about half placeholders.",
        """
## What a row is
One offense. A report (`report_number`) can contain several offenses: 1,568,385 rows cover 1,381,049 reports.

## Key fields
- `offense_category` (e.g. violent, property groupings), `offense_sub_category`, `nibrs_offense_code_description`, `nibrs_crime_against_category`.
- `offense_date`: when the offense started. Some rows carry placeholder dates as early as 1900-01-01.
- `report_date_time`: when it was reported, which can differ from `offense_date`.

## Area filters
- `precinct` (Title case: East, North, South, Southwest, West), `sector`, `beat`: about 99% assigned.
- `neighborhood`: `-` for nearly every offense from 2008 to 2016 (so about 48% of all rows), but only 1-4% missing from 2017 on. Neighborhood counts are fine for recent years and meaningless before 2017.
- `latitude`/`longitude` are text, blurred to the hundred block: `near` cannot be used.
""",
        (
            Example(
                "How many offenses of each category occurred in 2025?",
                {
                    "dataset_id": "tazs-3rd5",
                    "select": ["offense_category", "count(*) AS offenses"],
                    "where": "offense_date between '2025-01-01T00:00:00' and '2025-12-31T23:59:59'",
                    "group_by": ["offense_category"],
                    "order_by": ["offenses DESC"],
                },
            ),
            Example(
                "How many reports did each precinct take in 2025?",
                {
                    "dataset_id": "tazs-3rd5",
                    "select": ["precinct", "count(distinct report_number) AS reports"],
                    "where": "report_date_time between '2025-01-01T00:00:00' and '2025-12-31T23:59:59'",
                    "group_by": ["precinct"],
                    "order_by": ["reports DESC"],
                },
            ),
        ),
        rules=(
            "Count offenses with `count(*)` and reports with `count(distinct report_number)`.",
            "Always bound the date column to the period you mean: `offense_date` for when offenses occurred, `report_date_time` for when they were reported.",
            "For trends that include years before 2017, use precinct, sector, or beat, never `neighborhood`.",
        ),
    ),
    Guide(
        "building-permits",
        "76t5-zqzr",
        "Building Permits",
        "SDCI building permits issued or in progress, with locations, costs, and housing units.",
        """
## What a row is
One permit (`permitnum`). Filter views such as Issued Building Permits (`8tqq-u7ib`) are saved subsets of this dataset.

## Key fields
- `permitclass`, `permitclassmapped` (residential or not), `permittypemapped`, `permittypedesc`, `description`.
- `applieddate`, `issueddate`, `completeddate`, `expiresdate`: a permit with no `issueddate` has not been issued.
- `housingunitsadded`, `housingunitsremoved`, `housingunits` (net), `estprojectcost`.
- `statuscurrent`: latest lifecycle status.

## Area filters
- `location1` (location type): use with `near`, e.g. permits within 500 m of a point.
- `originalzip`: ZIP code as text.
""",
        (
            Example(
                "Which permits are within 500 m of downtown (Pike Place area)?",
                {
                    "dataset_id": "76t5-zqzr",
                    "select": ["permitnum", "permittypedesc", "issueddate", "statuscurrent"],
                    "near": {
                        "column": "location1",
                        "latitude": 47.6097,
                        "longitude": -122.3422,
                        "radius_m": 500,
                    },
                    "page_size": 10,
                },
            ),
            Example(
                "How many housing units were added by permits issued each year since 2015?",
                {
                    "dataset_id": "76t5-zqzr",
                    "select": [
                        "date_trunc_y(issueddate) AS year",
                        "sum(housingunitsadded) AS units",
                    ],
                    "where": "issueddate >= '2015-01-01T00:00:00'",
                    "group_by": ["date_trunc_y(issueddate)"],
                    "order_by": ["year"],
                },
            ),
        ),
    ),
    Guide(
        "fremont-bridge",
        "65db-xm6k",
        "Fremont Bridge Bicycle and Scooter Counter",
        "Hourly bike and scooter counts on the Fremont Bridge sidewalks since 2012; updated monthly.",
        """
## What a row is
One hour. `fremont_bridge` is the total for both sidewalks; it counts bikes and scooters together.

## Direction labels
`fremont_bridge_nb` and `fremont_bridge_sb` are per-sidewalk counts, not travel directions: the sensors count passes regardless of direction. The publisher's own metadata disagrees about which sidewalk each column is.

## Time
- `date`: the hour, Pacific wall-clock (`calendar_date` in metadata, `floating_timestamp` in query results).
- Updated monthly: on 2026-10-15 the newest hour was 2026-09-30T23:00.
""",
        (
            Example(
                "What were the monthly crossing totals, most recent first?",
                {
                    "dataset_id": "65db-xm6k",
                    "select": ["date_trunc_ym(date) AS month", "sum(fremont_bridge) AS crossings"],
                    "group_by": ["date_trunc_ym(date)"],
                    "order_by": ["month DESC"],
                    "page_size": 12,
                },
            ),
            Example(
                "How do weekday and weekend crossings compare in 2026?",
                {
                    "dataset_id": "65db-xm6k",
                    "select": [
                        "case(date_extract_dow(date) in (0, 6), 'weekend', true, 'weekday') AS day_type",
                        "sum(fremont_bridge) AS crossings",
                        "count(*) AS hours",
                    ],
                    "where": "date >= '2026-01-01T00:00:00'",
                    "group_by": [
                        "case(date_extract_dow(date) in (0, 6), 'weekend', true, 'weekday')"
                    ],
                },
            ),
        ),
        rules=(
            "Report `fremont_bridge` (the total) unless the user asks for a split; when you split, say the sidewalk labels are ambiguous.",
            'Check `data_coverage` before answering "this month"; never report a missing month as zero riders.',
        ),
    ),
    Guide(
        "use-of-force",
        "ppi5-g2bj",
        "SPD Use of Force",
        "Use-of-force records by Seattle Police officers; one row per subject, several per incident.",
        """
## What a row is
One subject in one use-of-force incident (`uniqueid`). 19,451 rows cover 18,444 incidents (`incident_num`); one incident has up to 9 rows.

## Key fields
- `incident_type`: Level 1 (most common), Level 2, Level 3, and Level 3 - OIS (officer-involved shooting).
- `occured_date_time` (sic): when force occurred; data starts in 2014 and lags about two weeks.
- `subject_race`, `subject_gender`, `officer_id`, `subject_id`.

## Area filters
- `precinct`, `sector`, `beat`. Precinct uses placeholders `-`, `0`, `X`, and `OOJ` (out of jurisdiction).
""",
        (
            Example(
                "How many use-of-force incidents of each level happened per year?",
                {
                    "dataset_id": "ppi5-g2bj",
                    "select": [
                        "date_trunc_y(occured_date_time) AS year",
                        "incident_type",
                        "count(distinct incident_num) AS incidents",
                    ],
                    "group_by": ["date_trunc_y(occured_date_time)", "incident_type"],
                    "order_by": ["year DESC", "incidents DESC"],
                    "page_size": 20,
                },
            ),
        ),
        rules=("Count incidents with `count(distinct incident_num)`.",),
    ),
    Guide(
        "road-weather",
        "egc4-d24i",
        "Road Weather Information Stations",
        "Road-surface and air temperatures at SDOT stations; meant to hold the last 48 hours.",
        """
## What a row is
One station reading: `roadsurfacetemperature` and `airtemperature` in degrees Fahrenheit, at `datetime`, for `stationname` (with `stationlocation`, a point).

## Freshness
The publisher says the dataset holds only the most recent 48 hours and updates hourly. On 2026-10-15 the newest reading was 2026-06-16T15:30 across 6 stations: the feed appears to have stopped.
""",
        (
            Example(
                "When did each station last report, and how many readings are there?",
                {
                    "dataset_id": "egc4-d24i",
                    "select": ["stationname", "max(datetime) AS latest", "count(*) AS readings"],
                    "group_by": ["stationname"],
                    "order_by": ["latest DESC"],
                },
            ),
        ),
        rules=(
            "Always check `data_coverage` and tell the user how old the readings are.",
            "Never present the readings as current conditions.",
        ),
    ),
)

BY_DATASET: dict[str, Guide] = {g.dataset_id: g for g in GUIDES}
BY_ID: dict[str, Guide] = {g.guide_id: g for g in GUIDES}

"""Seattle portal facts (PLAN §3). Extract a `Portal` type only when a second portal arrives."""

from __future__ import annotations

from zoneinfo import ZoneInfo

SERVER_NAME = "seattle-open-data"
TOOL_PREFIX = "seattle_data_"
URI_SCHEME = "seattle-data"

PUBLIC_HOST = "data.seattle.gov"
#: The Discovery API indexes Seattle under this name; `domains=data.seattle.gov` returns nothing.
CATALOG_DOMAIN = "cos-data.seattle.gov"
DISCOVERY_API = "https://api.us.socrata.com/api/catalog/v1"

SEATTLE_TZ = ZoneInfo("America/Los_Angeles")

DATASET_ID_PATTERN = r"^[a-z0-9]{4}-[a-z0-9]{4}$"

SEARCH_PAGE_SIZE_MAX = 50
QUERY_PAGE_SIZE_MAX = 500
QUERY_LOGICAL_CAP = 10_000
QUERY_TEXT_MAX_BYTES = 4096
SAMPLE_ROWS_MAX = 5

RESULT_BYTE_BUDGET = 24 * 1024
TOOLS_LIST_BYTE_BUDGET = 24 * 1024

CACHE_TTL_SECONDS = 3600

INSTRUCTIONS_SENTINEL = "END SEATTLE OPEN DATA INSTRUCTIONS"

UPSTREAM_TIMEOUT_SECONDS = 30.0
DESCRIBE_AGGREGATE_TIMEOUT_SECONDS = 10.0
UPSTREAM_MAX_BYTES = 8 * 1024 * 1024
CURSOR_TTL_SECONDS = 3600

#: Values publishers use instead of null in text place fields (PLAN §2).
PLACEHOLDER_VALUES = frozenset({"-", "UNKNOWN", "NULL", "OOJ", "FK ERROR", "X", "0", ""})

#: Computed-region layers seen on Seattle datasets; label columns and dates verified 2026-10-15.
REGION_LAYERS: dict[str, dict[str, str]] = {
    "ru88-fbhk": {
        "kind": "neighborhood",
        "label_column": "name",
        "vintage": "SPD Micro Community Policing Plan Areas, updated 2016-03-03",
        "reliability": "ok",
    },
    "kuhn-3gp2": {
        "kind": "beat",
        "label_column": "name",
        "vintage": "SPD Beats, updated 2015-07-17 (51 beats; current SPD beats differ)",
        "reliability": "partial",
        "reason": "2015 beat boundaries; current SPD beats differ.",
    },
    "q256-3sug": {
        "kind": "zip",
        "label_column": "zcta5ce10",
        "vintage": "2010 ZIP Code Tabulation Areas (national), updated 2015-02-19",
        "reliability": "ok",
    },
    "cyqu-gs94": {
        "kind": "council_district",
        "label_column": "c_district",
        "vintage": "City Council Districts, updated 2015-07-20 (predates 2022 redistricting)",
        "reliability": "unreliable",
        "reason": "Mostly null, only districts 1-3 appear, and the boundaries predate 2022.",
    },
    "2day-rhn5": {
        "kind": "census_tract",
        "label_column": "name10",
        "vintage": "2010 Census Tracts, updated 2015-05-16",
        "reliability": "unreliable",
        "reason": "Mostly null on Seattle datasets; 2010 tracts.",
    },
}

#: Text place fields per dataset, with reliability from the 2026-10-15 probe (PLAN §2).
PLACE_FIELDS: dict[str, dict[str, dict[str, str]]] = {
    "33kz-ixgy": {
        "dispatch_neighborhood": {"kind": "neighborhood", "reliability": "ok"},
        "dispatch_precinct": {"kind": "precinct", "reliability": "ok"},
        "dispatch_sector": {"kind": "sector", "reliability": "ok"},
        "dispatch_beat": {"kind": "beat", "reliability": "ok"},
    },
    "tazs-3rd5": {
        "neighborhood": {
            "kind": "neighborhood",
            "reliability": "partial",
            "reason": "About 48% of rows are '-'; neighborhood counts undercount badly.",
        },
        "precinct": {"kind": "precinct", "reliability": "ok"},
        "sector": {"kind": "sector", "reliability": "ok"},
        "beat": {"kind": "beat", "reliability": "ok"},
    },
    "ppi5-g2bj": {
        "precinct": {"kind": "precinct", "reliability": "ok"},
        "sector": {"kind": "sector", "reliability": "ok"},
        "beat": {"kind": "beat", "reliability": "ok"},
    },
    "76t5-zqzr": {
        "originalzip": {"kind": "zip", "reliability": "ok"},
    },
}

#: Timezone evidence per dataset column; absent means unknown, never assumed.
TIME_EVIDENCE: dict[str, dict[str, dict[str, str]]] = {
    "kzjm-xkqj": {
        "datetime": {
            "timezone": "America/Los_Angeles",
            "evidence": "max(datetime) tracked Pacific wall-clock time when probed 2026-10-15.",
        },
    },
}

GEO_TYPES = frozenset({"point", "location"})
TIME_TYPES = frozenset({"floating_timestamp", "calendar_date", "fixed_timestamp", "date"})

#: Upper bound written into download_url; SODA2 accepted it when probed 2026-10-15.
DOWNLOAD_LIMIT = 1_000_000_000
URL_MAX_CHARS = 2048
#: Below this assigned share a place field earns a coverage warning (PLAN §4).
COVERAGE_WARNING_THRESHOLD = 0.9

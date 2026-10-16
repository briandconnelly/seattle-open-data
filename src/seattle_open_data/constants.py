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

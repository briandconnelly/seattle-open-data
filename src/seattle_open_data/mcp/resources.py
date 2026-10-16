"""Static reference resources (docs/design.md: Primitives).

Same content as `seattle_data_get_reference`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from seattle_open_data.constants import URI_SCHEME
from seattle_open_data.core import reference_service
from seattle_open_data.core.reference import CAPABILITIES

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP


def _topic_index(topic: str) -> str:
    return json.dumps(
        reference_service.read(topic, None).model_dump(
            mode="json", exclude_none=True, exclude={"source"}
        )
    )


def _index_reader(topic: str) -> Callable[[], str]:
    # A default-argument lambda would be read as a URI template parameter.
    def read() -> str:
        return _topic_index("index" if topic == "reference" else topic)

    return read


def _item(topic: str, item_id: str) -> str:
    result = reference_service.read(topic, item_id)
    return result.body or ""


def register_resources(mcp: FastMCP) -> None:
    @mcp.resource(
        f"{URI_SCHEME}://capabilities",
        name="capabilities",
        title="Capability summary",
        description="What the server does and does not do, binding rules, and failure "
        "carriers. Read first.",
        mime_type="text/markdown",
    )
    def capabilities() -> str:
        return CAPABILITIES

    for topic, title, description in (
        (
            "reference",
            "Reference index",
            "Every reference topic: capabilities, guides, soql, errors.",
        ),
        (
            "soql",
            "SoQL reference index",
            "Sections on syntax, filtering, aggregation, dates, geography, and paging.",
        ),
        (
            "errors",
            "Error catalog index",
            "The error envelope, codes by tool, and authentication repair.",
        ),
        ("guides", "Dataset guide index", "Curated guides for flagship Seattle datasets."),
    ):
        mcp.resource(
            f"{URI_SCHEME}://{topic}",
            name=f"{topic}-index",
            title=title,
            description=description,
            mime_type="application/json",
        )(_index_reader(topic))

    @mcp.resource(
        f"{URI_SCHEME}://soql/{{section_id}}",
        name="soql-section",
        title="SoQL reference section",
        description="One SoQL section; ids are listed at seattle-data://soql.",
        mime_type="text/markdown",
    )
    def soql_section(section_id: str) -> str:
        return _item("soql", section_id)

    @mcp.resource(
        f"{URI_SCHEME}://errors/{{section_id}}",
        name="errors-section",
        title="Error catalog section",
        description="One error-catalog section: envelope, codes, or authentication.",
        mime_type="text/markdown",
    )
    def errors_section(section_id: str) -> str:
        return _item("errors", section_id)

    @mcp.resource(
        f"{URI_SCHEME}://guides/{{guide_id}}",
        name="guide",
        title="Dataset guide",
        description="A curated dataset guide; ids are listed at seattle-data://guides.",
        mime_type="text/markdown",
    )
    def guide(guide_id: str) -> str:
        return _item("guides", guide_id)

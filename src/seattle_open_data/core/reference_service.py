"""Read static reference content for the reference tool and the resources."""

from __future__ import annotations

from seattle_open_data.core.context import make_source
from seattle_open_data.core.errors import ErrorCode, ErrorDetails, Repair, SeattleDataError
from seattle_open_data.core.models import ReferenceEntry, ReferenceResult
from seattle_open_data.core.reference import (
    CAPABILITIES,
    CONTENT_VERSION,
    TOPICS,
    topic_uri,
)

_TOOL = "seattle_data_get_reference"


def read(topic: str, item_id: str | None, chunk_id: str | None) -> ReferenceResult:
    if topic == "index":
        return ReferenceResult(
            topic="index",
            uri=topic_uri("reference"),
            title="seattle-open-data reference",
            summary="Topics: capabilities, guides, soql, errors.",
            version=CONTENT_VERSION,
            entries=[
                ReferenceEntry(
                    item_id=t.topic, title=t.title, description=t.summary, uri=topic_uri(t.topic)
                )
                for t in TOPICS.values()
            ],
            source=make_source(),
        )
    if topic == "capabilities":
        return ReferenceResult(
            topic="capabilities",
            uri=topic_uri("capabilities"),
            title=TOPICS["capabilities"].title,
            summary=TOPICS["capabilities"].summary,
            version=CONTENT_VERSION,
            body=CAPABILITIES,
            source=make_source(),
        )

    entry = TOPICS[topic]
    if item_id is None:
        return ReferenceResult(
            topic=topic,
            uri=topic_uri(topic),
            title=entry.title,
            summary=entry.summary,
            version=CONTENT_VERSION,
            entries=[
                ReferenceEntry(
                    item_id=d.item_id,
                    title=d.title,
                    description=d.description,
                    uri=topic_uri(topic, d.item_id),
                )
                for d in entry.documents
            ],
            source=make_source(),
        )

    document = next((d for d in entry.documents if d.item_id == item_id), None)
    if document is None:
        raise SeattleDataError(
            ErrorCode.REFERENCE_NOT_FOUND,
            f"No reference item {item_id!r} under topic {topic!r}.",
            details=ErrorDetails(
                field="item_id",
                reason="Unknown item.",
                candidates=[d.item_id for d in entry.documents] or None,
            ),
            repair=Repair(next_step="list_items", tool=_TOOL, arguments={"topic": topic}),
        )
    if chunk_id is not None:
        raise SeattleDataError(
            ErrorCode.REFERENCE_NOT_FOUND,
            f"Reference item {item_id!r} is not chunked.",
            details=ErrorDetails(field="chunk_id", reason="This item has a single body."),
            repair=Repair(
                next_step="read_item", tool=_TOOL, arguments={"topic": topic, "item_id": item_id}
            ),
        )
    return ReferenceResult(
        topic=topic,
        uri=topic_uri(topic, item_id),
        title=document.title,
        summary=document.description,
        version=CONTENT_VERSION,
        body=document.body,
        source=make_source(),
    )

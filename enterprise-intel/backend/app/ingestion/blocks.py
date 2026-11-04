from dataclasses import dataclass, field
from typing import Any


class IngestionError(Exception):
    """A document that cannot be processed; the message is shown to the user."""


@dataclass
class Block:
    """Common output of every extractor.

    type is one of: line (unclassified text line), blank, heading, paragraph, code,
    table, csv_schema, csv_row.
    """

    type: str
    text: str = ""
    level: int = 0
    page: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractResult:
    blocks: list[Block]
    pages: int = 0
    images_removed: int = 0

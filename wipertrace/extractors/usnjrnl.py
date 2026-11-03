"""$UsnJrnl:$J extractor (MFTECmd CSV)."""

from __future__ import annotations

from collections.abc import Iterable

from wipertrace.extractors.base import Extractor
from wipertrace.models import Event, Origin
from wipertrace.util import parse_ts, split_ops, to_int


class UsnJrnlExtractor(Extractor):
    artifact = "usnjrnl"
    required = ("name", "reasons")

    def parse_row(self, row: dict[str, str], origin: Origin) -> Iterable[Event]:
        attrs = self.col(row, "file_attributes") or ""
        usn = to_int(self.col(row, "usn"))
        yield Event(
            source=self.artifact,
            ops=split_ops(self.col(row, "reasons")),
            timestamp=parse_ts(self.col(row, "timestamp")),
            name=self.col(row, "name"),
            file_ref=to_int(self.col(row, "entry")),
            seq=to_int(self.col(row, "seq")),
            parent_ref=to_int(self.col(row, "parent")),
            attrs={
                "usn": usn,
                "file_attributes": attrs,
                "is_directory": "directory" in attrs.lower(),
            },
            origin=origin,
            # USN is monotonically increasing; fall back to row order if absent.
            order=usn if usn is not None else origin.row,
        )

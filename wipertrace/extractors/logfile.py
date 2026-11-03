"""$LogFile extractor.

Defaults match LogFileParser (jschicht) column names. For output from a
different parser, map its columns with --columns (see wipertrace/config.py).
Each log record becomes one event whose op is the canonical redo operation.
"""

from __future__ import annotations

from collections.abc import Iterable

from wipertrace.extractors.base import Extractor
from wipertrace.models import Event, Origin
from wipertrace.util import canon_op, parse_ts, to_int


class LogFileExtractor(Extractor):
    artifact = "logfile"
    required = ("redo_op",)

    def parse_row(self, row: dict[str, str], origin: Origin) -> Iterable[Event]:
        redo = canon_op(self.col(row, "redo_op") or "")
        undo = canon_op(self.col(row, "undo_op") or "")
        lsn = to_int(self.col(row, "lsn"))
        yield Event(
            source=self.artifact,
            ops=frozenset({redo}) if redo else frozenset(),
            timestamp=parse_ts(self.col(row, "timestamp")),
            name=self.col(row, "name"),
            file_ref=to_int(self.col(row, "file_ref")),
            attrs={"lsn": lsn, "redo_op": redo, "undo_op": undo},
            origin=origin,
            order=lsn if lsn is not None else origin.row,
        )

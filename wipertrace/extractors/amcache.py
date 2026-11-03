"""Amcache extractor (AmcacheParser *FileEntries CSV).

Amcache records presence/inventory, not a run time, so events carry the op
PRESENT. Amcache is unreliable for short-lived executions because it is
populated by a scheduled inventory task, so rules only ever use it as an
alternative to Prefetch.
"""

from __future__ import annotations

import ntpath
from collections.abc import Iterable

from wipertrace.extractors.base import Extractor
from wipertrace.models import Event, Origin
from wipertrace.util import parse_ts


class AmcacheExtractor(Extractor):
    artifact = "amcache"
    required = ()

    def parse_row(self, row: dict[str, str], origin: Origin) -> Iterable[Event]:
        path = self.col(row, "path") or ""
        name = ntpath.basename(path) if path else (self.col(row, "name") or "")
        if not name:
            return
        yield Event(
            source=self.artifact,
            ops=frozenset({"PRESENT"}),
            timestamp=parse_ts(self.col(row, "timestamp")),
            name=name.upper(),
            attrs={"path": path, "sha1": self.col(row, "sha1")},
            origin=origin,
        )

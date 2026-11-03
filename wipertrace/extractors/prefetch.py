"""Prefetch extractor (PECmd CSV): one EXECUTION event per recorded run time."""

from __future__ import annotations

from collections.abc import Iterable

from wipertrace.extractors.base import Extractor
from wipertrace.models import Event, Origin
from wipertrace.util import parse_ts, to_int


class PrefetchExtractor(Extractor):
    artifact = "prefetch"
    required = ("exe",)

    def parse_row(self, row: dict[str, str], origin: Origin) -> Iterable[Event]:
        exe = (self.col(row, "exe") or "").upper()
        run_cols = self.cols.get("previous_runs") or []
        stamps = [self.col(row, "last_run")] + [row.get(c) for c in run_cols]  # type: ignore[union-attr]
        times = [t for t in (parse_ts(s) for s in stamps) if t]
        base = {
            "run_count": to_int(self.col(row, "run_count")),
            "pf_file": self.col(row, "source_file"),
            "hash": self.col(row, "hash"),
        }
        if not times:
            yield Event(self.artifact, frozenset({"EXECUTION"}), None, exe, attrs=dict(base), origin=origin)
            return
        for i, t in enumerate(times):
            yield Event(
                self.artifact,
                frozenset({"EXECUTION"}),
                t,
                exe,
                attrs={**base, "run_index": i},  # 0 = LastRun
                origin=origin,
            )

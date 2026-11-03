"""Extractor base class: tolerant CSV reading and column resolution."""

from __future__ import annotations

import csv
import sys
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from pathlib import Path

from wipertrace.models import Event, Origin

# Prefetch FilesLoaded / Directories cells can be very large.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


class ExtractorError(Exception):
    pass


class Extractor(ABC):
    """Turns one parser's CSV output into normalised Events.

    Subclasses set `artifact`, `required` (logical columns that must exist) and
    implement `parse_row`. Column candidates come from `wipertrace.config.COLUMNS`.
    """

    artifact: str = ""
    required: tuple[str, ...] = ()

    def __init__(self, columns: dict[str, list[str]]):
        self.candidates = columns
        self.cols: dict[str, str | list[str]] = {}

    # ---- column handling -------------------------------------------------
    def _resolve(self, header: list[str], path: Path) -> None:
        lower = {h.strip().lower(): h for h in header}
        self.cols = {}
        for logical, cands in self.candidates.items():
            if logical == "previous_runs":  # multi-column logical field
                self.cols[logical] = [lower[c.lower()] for c in cands if c.lower() in lower]
                continue
            for c in cands:
                if c.lower() in lower:
                    self.cols[logical] = lower[c.lower()]
                    break
        missing = [r for r in self.required if r not in self.cols]
        if missing:
            detail = "; ".join(f"{m} (tried {self.candidates.get(m)})" for m in missing)
            raise ExtractorError(
                f"{path.name}: not a recognised {self.artifact} CSV — missing columns: {detail}. "
                f"Use --columns to map your parser's header names."
            )

    def col(self, row: dict[str, str], logical: str) -> str | None:
        name = self.cols.get(logical)
        if not name or isinstance(name, list):
            return None
        v = row.get(name)
        return v if v not in (None, "") else None

    # ---- reading ---------------------------------------------------------
    def rows(self, path: Path) -> Iterator[tuple[int, dict[str, str]]]:
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return
            self._resolve(list(reader.fieldnames), path)
            for i, row in enumerate(reader, start=1):
                yield i, row

    def extract(self, paths: list[Path]) -> list[Event]:
        events: list[Event] = []
        for path in paths:
            for row_no, row in self.rows(path):
                origin = Origin(self.artifact, str(path), row_no)
                for ev in self.parse_row(row, origin):
                    if not ev.order:
                        ev.order = row_no
                    events.append(ev)
        events.sort(key=lambda e: e.order)
        return events

    @abstractmethod
    def parse_row(self, row: dict[str, str], origin: Origin) -> Iterable[Event]:
        ...

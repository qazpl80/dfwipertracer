"""Evaluation context: the event store plus cached groupings shared by matchers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from wipertrace.models import Event, RuleResult


@dataclass
class RenameChain:
    """All renames of one file (MFT entry + sequence) in journal order."""

    key: tuple[int | None, int | None]
    original: str | None = None           # name before the first rename
    renames: list[Event] = field(default_factory=list)   # RENAME_NEW_NAME events, de-duplicated
    events: list[Event] = field(default_factory=list)    # old+new name events (evidence)

    @property
    def names(self) -> list[str]:
        return [e.name or "" for e in self.renames]


class EvalContext:
    def __init__(self, store: dict[str, list[Event]], available: set[str]):
        self.store = store
        self.available = available
        self.results: dict[str, RuleResult] = {}
        self._chains: dict | None = None
        self._by_file: dict[str, dict] = {}

    def events(self, source: str) -> list[Event]:
        return self.store.get(source, []) if source in self.available else []

    def rename_chains(self) -> dict[tuple, RenameChain]:
        if self._chains is not None:
            return self._chains
        chains: dict[tuple, RenameChain] = {}
        for ev in self.events("usnjrnl"):
            if ev.file_ref is None:
                continue
            is_old = "RENAME_OLD_NAME" in ev.ops
            is_new = "RENAME_NEW_NAME" in ev.ops
            if not (is_old or is_new):
                continue
            ch = chains.setdefault(ev.file_key, RenameChain(ev.file_key))
            if is_old:
                if ch.original is None and not ch.renames:
                    ch.original = ev.name
                ch.events.append(ev)
            if is_new:
                # MFTECmd emits RenameNewName and RenameNewName|Close for one rename.
                if ch.renames and ch.renames[-1].name == ev.name:
                    continue
                ch.renames.append(ev)
                ch.events.append(ev)
        self._chains = {k: v for k, v in chains.items() if v.renames}
        return self._chains

    def by_file(self, source: str) -> dict[int, list[Event]]:
        """Events of one source grouped by MFT entry number."""
        if source not in self._by_file:
            groups: dict[int, list[Event]] = defaultdict(list)
            for ev in self.events(source):
                if ev.file_ref is not None:
                    groups[ev.file_ref].append(ev)
            self._by_file[source] = dict(groups)
        return self._by_file[source]

    @staticmethod
    def times(events: list[Event]) -> list[datetime]:
        return sorted(e.timestamp for e in events if e.timestamp is not None)

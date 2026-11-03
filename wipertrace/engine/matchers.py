"""Generic, parameterised matchers that signature rules are built from.

A matcher never knows which tool it describes; the YAML rule supplies the
parameters. Adding a new wiping tool therefore needs only new YAML, unless it
exhibits a behaviour none of these kinds can express.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from wipertrace import ARTIFACTS
from wipertrace.engine.context import EvalContext
from wipertrace.models import Event
from wipertrace.util import canon_op, iso


class RuleParamError(ValueError):
    pass


@dataclass
class MatchOutcome:
    matched: bool
    strength: float = 0.0
    events: list[Event] = field(default_factory=list)      # evidence events
    extra: list[dict[str, Any]] = field(default_factory=list)  # non-event evidence
    detail: str = ""


class Matcher(ABC):
    kind: str = ""
    allowed: set[str] = set()

    def __init__(self, params: dict[str, Any]):
        unknown = set(params) - self.allowed
        if unknown:
            raise RuleParamError(f"unknown params for {self.kind}: {sorted(unknown)}")
        self.p = params
        self.setup()

    def setup(self) -> None:  # validate / compile
        pass

    @abstractmethod
    def evaluate(self, ctx: EvalContext) -> MatchOutcome:
        ...

    @staticmethod
    def _range(value: Any, name: str) -> tuple[float | None, float | None]:
        if not (isinstance(value, (list, tuple)) and len(value) == 2):
            raise RuleParamError(f"{name} must be [min, max] (use null for open-ended)")
        return value[0], value[1]

    @staticmethod
    def _in_range(n: float, rng: tuple[float | None, float | None]) -> bool:
        lo, hi = rng
        return (lo is None or n >= lo) and (hi is None or n <= hi)


# --------------------------------------------------------------------------- #
class RenameChainMatcher(Matcher):
    """Per-file rename behaviour in $UsnJrnl.

    params:
      min_renames / max_renames   distinct new names per file
      name_regex                  pattern the new names must fully match
      min_regex_matches           how many names must match (default: all)
      letter_progression          min run of names whose first letter increments (A,B,C…)
      same_length_as_original     every new name has the original name's length
      length_sequence             [[min,max], …] length of the 1st, 2nd … new name
      max_span_seconds            first-to-last rename within this many seconds
      exclude_name_regex          skip chains where any new name matches (e.g. Recycle Bin $R…)
      min_files                   files that must show the chain (default 1)
    """

    kind = "rename_chain"
    allowed = {
        "min_renames", "max_renames", "name_regex", "min_regex_matches", "letter_progression",
        "same_length_as_original", "length_sequence", "max_span_seconds", "exclude_name_regex",
        "min_files",
    }

    def setup(self) -> None:
        self.rx = re.compile(self.p["name_regex"]) if "name_regex" in self.p else None
        self.exclude = re.compile(self.p["exclude_name_regex"]) if "exclude_name_regex" in self.p else None
        self.lengths = [self._range(r, "length_sequence item") for r in self.p.get("length_sequence", [])]

    @staticmethod
    def _longest_progression(names: list[str]) -> int:
        best = run = 0
        prev = None
        for nm in names:
            c = nm[:1].upper()
            run = run + 1 if prev and c and ord(c) == ord(prev) + 1 else 1
            best = max(best, run)
            prev = c
        return best

    def _chain_ok(self, ch) -> bool:
        p = self.p
        names = ch.names
        n = len(names)
        if n < p.get("min_renames", 1):
            return False
        if "max_renames" in p and n > p["max_renames"]:
            return False
        if self.exclude and any(self.exclude.search(nm) for nm in names):
            return False
        considered = names
        if self.rx:
            considered = [nm for nm in names if self.rx.fullmatch(nm)]
            if len(considered) < p.get("min_regex_matches", n):
                return False
        if "letter_progression" in p and self._longest_progression(considered) < p["letter_progression"]:
            return False
        if p.get("same_length_as_original"):
            if not ch.original or any(len(nm) != len(ch.original) or nm == ch.original for nm in names):
                return False
        if self.lengths:
            if n < len(self.lengths):
                return False
            if not all(self._in_range(len(names[i]), r) for i, r in enumerate(self.lengths)):
                return False
        if "max_span_seconds" in p:
            ts = EvalContext.times(ch.renames)
            if len(ts) >= 2 and (ts[-1] - ts[0]).total_seconds() > p["max_span_seconds"]:
                return False
        return True

    def evaluate(self, ctx: EvalContext) -> MatchOutcome:
        hits = [ch for ch in ctx.rename_chains().values() if self._chain_ok(ch)]
        need = self.p.get("min_files", 1)
        events = [e for ch in hits for e in ch.events]
        return MatchOutcome(
            matched=len(hits) >= need,
            strength=1.0 if len(hits) >= need else 0.0,
            events=events,
            detail=f"{len(hits)} file(s) with a matching rename chain (need >= {need})",
        )


# --------------------------------------------------------------------------- #
class PerFileCountMatcher(Matcher):
    """Per-file operation counts within one artifact.

    params:
      source      artifact name
      counts      {OP: [min, max]}; key "A|B" sums events having A or B
      min_files   files that must satisfy all counts (default 1)
    """

    kind = "per_file_count"
    allowed = {"source", "counts", "min_files"}

    def setup(self) -> None:
        if self.p.get("source") not in ARTIFACTS:
            raise RuleParamError(f"source must be one of {ARTIFACTS}")
        counts = self.p.get("counts")
        if not isinstance(counts, dict) or not counts:
            raise RuleParamError("counts must be a non-empty mapping")
        self.counts = [
            (frozenset(canon_op(a) for a in key.split("|")), self._range(rng, f"counts.{key}"), key)
            for key, rng in counts.items()
        ]

    def evaluate(self, ctx: EvalContext) -> MatchOutcome:
        hits: list[tuple[int, list[Event]]] = []
        for ref, evs in ctx.by_file(self.p["source"]).items():
            ok = True
            for alts, rng, _ in self.counts:
                n = sum(1 for e in evs if e.ops & alts)
                if not self._in_range(n, rng):
                    ok = False
                    break
            if ok:
                hits.append((ref, evs))
        need = self.p.get("min_files", 1)
        events = [e for _, evs in hits for e in evs if any(e.ops & alts for alts, _, _ in self.counts)]
        return MatchOutcome(
            matched=len(hits) >= need,
            strength=1.0 if len(hits) >= need else 0.0,
            events=events,
            detail=f"{len(hits)} file(s) satisfy {', '.join(k for *_, k in self.counts)} (need >= {need})",
        )


# --------------------------------------------------------------------------- #
class _Predicate:
    OPS = {"regex", "iregex", "eq", "ne", "in", "range", "prefix", "contains", "exists"}

    def __init__(self, fieldname: str, spec: Any):
        self.field = fieldname
        if not isinstance(spec, dict):
            spec = {"eq": spec}
        bad = set(spec) - self.OPS
        if bad:
            raise RuleParamError(f"unknown predicate(s) {sorted(bad)} on field {fieldname}")
        self.spec = dict(spec)
        if "regex" in spec:
            self.spec["regex"] = re.compile(spec["regex"])
        if "iregex" in spec:
            self.spec["iregex"] = re.compile(spec["iregex"], re.IGNORECASE)

    def __call__(self, ev: Event) -> bool:
        v = ev.get(self.field)
        for op, arg in self.spec.items():
            if op == "exists":
                if (v not in (None, "")) != bool(arg):
                    return False
                continue
            if op == "eq":
                if v != arg:
                    return False
                continue
            if op == "ne":
                if v == arg:
                    return False
                continue
            if v is None:
                return False
            if op in ("regex", "iregex") and not arg.search(str(v)):
                return False
            if op == "in" and v not in arg:
                return False
            if op == "prefix" and not str(v).startswith(arg):
                return False
            if op == "contains" and str(arg).lower() not in str(v).lower():
                return False
            if op == "range":
                lo, hi = arg
                if (lo is not None and v < lo) or (hi is not None and v > hi):
                    return False
        return True


class MatchMatcher(Matcher):
    """Events whose fields satisfy predicates.

    params:
      sources     [artifact, …]
      ops         [OP, …]   (any-of; optional)
      where       {field: value | {regex|iregex|eq|ne|in|range|prefix|contains|exists: …}}
      min_count   matching events required (default 1); per file if per_file
      per_file    count per MFT entry instead of globally
      min_files   with per_file: files that must reach min_count (default 1)
    """

    kind = "match"
    allowed = {"sources", "ops", "where", "min_count", "per_file", "min_files"}

    def setup(self) -> None:
        srcs = self.p.get("sources")
        if not srcs or any(s not in ARTIFACTS for s in srcs):
            raise RuleParamError(f"sources must be a list drawn from {ARTIFACTS}")
        self.ops = frozenset(canon_op(o) for o in self.p.get("ops", []))
        self.preds = [_Predicate(f, s) for f, s in (self.p.get("where") or {}).items()]

    def evaluate(self, ctx: EvalContext) -> MatchOutcome:
        hits = [
            e
            for src in self.p["sources"]
            for e in ctx.events(src)
            if (not self.ops or e.ops & self.ops) and all(pr(e) for pr in self.preds)
        ]
        need = self.p.get("min_count", 1)
        if self.p.get("per_file"):
            per = Counter(e.file_ref for e in hits if e.file_ref is not None)
            files = {ref for ref, n in per.items() if n >= need}
            need_files = self.p.get("min_files", 1)
            ok = len(files) >= need_files
            hits = [e for e in hits if e.file_ref in files]
            detail = f"{len(files)} file(s) with >= {need} matching event(s) (need >= {need_files})"
        else:
            ok = len(hits) >= need
            detail = f"{len(hits)} matching event(s) (need >= {need})"
        return MatchOutcome(matched=ok, strength=1.0 if ok else 0.0, events=hits, detail=detail)


# --------------------------------------------------------------------------- #
class CorrelateMatcher(Matcher):
    """Cross-artifact corroboration: all referenced rules matched AND their
    evidence lies within `window_seconds` of each other (pairwise).

    params:
      rules           [rule_id, …]  (non-correlate rules)
      window_seconds  max gap between the closest timestamps of each pair
    """

    kind = "correlate"
    allowed = {"rules", "window_seconds"}

    def setup(self) -> None:
        if not isinstance(self.p.get("rules"), list) or len(self.p["rules"]) < 2:
            raise RuleParamError("rules must list at least two rule ids")
        if "window_seconds" not in self.p:
            raise RuleParamError("window_seconds is required")

    @staticmethod
    def _closest(a: list[datetime], b: list[datetime]) -> tuple[float, datetime | None, datetime | None]:
        i = j = 0
        best = (float("inf"), None, None)
        while i < len(a) and j < len(b):
            gap = abs((a[i] - b[j]).total_seconds())
            if gap < best[0]:
                best = (gap, a[i], b[j])
            if a[i] < b[j]:
                i += 1
            else:
                j += 1
        return best

    def evaluate(self, ctx: EvalContext) -> MatchOutcome:
        ids = self.p["rules"]
        results = [ctx.results.get(r) for r in ids]
        if any(r is None or r.status != "matched" for r in results):
            unmatched = [i for i, r in zip(ids, results) if r is None or r.status != "matched"]
            return MatchOutcome(False, detail=f"prerequisite rule(s) not matched: {unmatched}")
        extra, worst = [], 0.0
        for x in range(len(results)):
            for y in range(x + 1, len(results)):
                gap, ta, tb = self._closest(results[x].evidence_times, results[y].evidence_times)
                if ta is None:
                    return MatchOutcome(False, detail=f"no timestamps to correlate {ids[x]} with {ids[y]}")
                worst = max(worst, gap)
                extra.append({"pair": [ids[x], ids[y]], "gap_seconds": round(gap, 3),
                              "times": [iso(ta), iso(tb)]})
        ok = worst <= self.p["window_seconds"]
        return MatchOutcome(
            matched=ok,
            strength=1.0 if ok else 0.0,
            extra=extra,
            detail=f"largest closest-gap {worst:.1f}s (window {self.p['window_seconds']}s)",
        )


KINDS: dict[str, type[Matcher]] = {
    m.kind: m for m in (RenameChainMatcher, PerFileCountMatcher, MatchMatcher, CorrelateMatcher)
}

"""End-to-end analysis: case -> extract -> evaluate -> score -> AnalysisResult."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from wipertrace import ARTIFACTS, __version__
from wipertrace.case import Case
from wipertrace.config import load_column_overrides
from wipertrace.engine import EvalContext, evaluate, load_signatures
from wipertrace.extractors import REGISTRY, ExtractorError
from wipertrace.models import AnalysisResult, ArtifactStatus, Event
from wipertrace.scoring import conclude, score

log = logging.getLogger("wipertrace")

STATIC_LIMITATIONS = [
    "Full-disk offline wipers (e.g. DBAN) leave no live-OS artifacts and cannot be attributed.",
    "A wiper that corrupts NTFS metadata files can defeat journal-based rules.",
    "$UsnJrnl and $LogFile are circular; late acquisition can lose the relevant records.",
    "Overwrite settings (e.g. SDelete -p1 vs -p3, Eraser Gutmann vs pseudorandom) are "
    "indistinguishable in metadata; attribution is at tool level only.",
    "Amcache is populated by a scheduled inventory task and is unreliable for short runs.",
    "Signatures were derived in a lab from one build per tool on Windows NTFS; other "
    "versions may behave differently.",
]


def extract_all(
    case: Case,
    columns_path: Path | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[dict[str, list[Event]], list[ArtifactStatus]]:
    columns = load_column_overrides(columns_path)
    store: dict[str, list[Event]] = {}
    statuses: list[ArtifactStatus] = []
    for artifact in ARTIFACTS:
        paths = case.inputs.get(artifact) or []
        st = ArtifactStatus(artifact=artifact, state="missing", files=[str(p) for p in paths])
        if paths:
            try:
                events = REGISTRY[artifact](columns[artifact]).extract(paths)
            except (ExtractorError, OSError) as exc:
                st.state, st.message = "error", str(exc)
                log.warning("%s: %s", artifact, exc)
                events = []
            else:
                if since or until:
                    events = [
                        e
                        for e in events
                        if e.timestamp is None
                        or ((not since or e.timestamp >= since) and (not until or e.timestamp <= until))
                    ]
                st.events = len(events)
                # an artifact with zero records (e.g. rolled-over journal) is treated as
                # unavailable so its rules become not_evaluable, not silently negative
                st.state = "present" if events else "empty"
                if not events:
                    st.message = "parsed, but contains no records (rolled over or wiped?)"
            store[artifact] = events
        statuses.append(st)
    return store, statuses


def analyze(
    case: Case,
    signatures_path: Path | None = None,
    columns_path: Path | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    thresholds: dict[str, float] | None = None,
) -> AnalysisResult:
    db = load_signatures(signatures_path)
    store, statuses = extract_all(case, columns_path, since, until)
    available = {s.artifact for s in statuses if s.state == "present"}
    ctx = EvalContext(store, available)
    results = evaluate(db, ctx)
    verdicts = score(db, results)
    conclusion = conclude(verdicts, statuses, thresholds)

    limitations = list(STATIC_LIMITATIONS)
    for s in statuses:
        if s.state != "present":
            limitations.append(f"{s.artifact}: {s.state}" + (f" — {s.message}" if s.message else "")
                               + "; rules needing it were not evaluated.")
    if db.meta["uncalibrated_rules"]:
        uncal = [r.rule_id for r in results if not r.calibrated and r.status == "matched"]
        if uncal:
            limitations.append(
                "Matched rules with provisional (uncalibrated) thresholds: " + ", ".join(uncal) + "."
            )

    meta = case.describe()
    meta.update(
        {
            "wipertrace_version": __version__,
            "time_filter": {"since": since.isoformat() if since else None, "until": until.isoformat() if until else None},
            "inputs": case.manifest(),
        }
    )
    return AnalysisResult(
        case=meta,
        signature_db=db.meta,
        artifacts=statuses,
        rule_results=results,
        verdicts=verdicts,
        conclusion=conclusion,
        limitations=limitations,
    )

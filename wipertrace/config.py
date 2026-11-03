"""Default parser column names and case-directory discovery patterns.

Parsers change column names between versions. Each logical column maps to a list
of candidate header names; the first one present in the CSV wins. Override any of
them with `wipertrace analyze --columns my_columns.yaml`:

    logfile:
      redo_op: [MyRedoColumn]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# logical column -> candidate CSV headers (case-insensitive match)
COLUMNS: dict[str, dict[str, list[str]]] = {
    # MFTECmd -f $J --csv
    "usnjrnl": {
        "name": ["Name", "FileName", "name"],
        "entry": ["EntryNumber", "MFTEntry", "file_ref", "FileReferenceNumber"],
        "seq": ["SequenceNumber", "seq"],
        "parent": ["ParentEntryNumber", "parent_ref"],
        "usn": ["UpdateSequenceNumber", "USN", "usn"],
        "timestamp": ["UpdateTimestamp", "TimeStamp", "Timestamp", "timestamp"],
        "reasons": ["UpdateReasons", "Reason", "Reasons", "reasons"],
        "file_attributes": ["FileAttributes", "file_attributes"],
    },
    # MFTECmd -f $MFT --csv
    "mft": {
        "name": ["FileName", "Name", "name"],
        "entry": ["EntryNumber", "file_ref"],
        "seq": ["SequenceNumber", "seq"],
        "parent": ["ParentEntryNumber", "parent_ref"],
        "parent_path": ["ParentPath", "parent_path"],
        "in_use": ["InUse", "in_use"],
        "is_directory": ["IsDirectory", "is_directory"],
        "size": ["FileSize", "size"],
        "si_created": ["Created0x10"],
        "si_modified": ["LastModified0x10"],
        "si_record_change": ["LastRecordChange0x10"],
        "si_accessed": ["LastAccess0x10"],
        "fn_created": ["Created0x30"],
        "fn_modified": ["LastModified0x30"],
        "fn_record_change": ["LastRecordChange0x30"],
        "fn_accessed": ["LastAccess0x30"],
    },
    # Defaults follow LogFileParser (jschicht) LogFile.csv; adjust to your parser.
    "logfile": {
        "lsn": ["lf_LSN", "LSN", "lsn", "this_lsn"],
        "file_ref": ["lf_MFTReference", "lf_RealMFTReference", "MFTReference", "file_ref", "target_mft"],
        "redo_op": ["lf_RedoOperation", "RedoOperation", "redo_op", "redo"],
        "undo_op": ["lf_UndoOperation", "UndoOperation", "undo_op", "undo"],
        "name": ["lf_FileName", "FileName", "name"],
        "timestamp": ["lf_Timestamp", "timestamp"],
    },
    # PECmd -d <Prefetch> --csv
    "prefetch": {
        "exe": ["ExecutableName", "Executable", "exe"],
        "run_count": ["RunCount", "run_count"],
        "last_run": ["LastRun", "last_run"],
        "previous_runs": [f"PreviousRun{i}" for i in range(7)],  # multi-column
        "source_file": ["SourceFilename", "SourceFile"],
        "hash": ["Hash"],
    },
    # AmcacheParser -f Amcache.hve --csv  (*FileEntries.csv)
    "amcache": {
        "path": ["FullPath", "LowerCaseLongPath", "Path", "path"],
        "name": ["Name", "FileName"],
        "sha1": ["SHA1", "sha1"],
        "timestamp": [
            "FileKeyLastWriteTimestamp",
            "FileIDLastWriteTimestamp",
            "KeyLastWriteTimestamp",
            "timestamp",
        ],
    },
}

# Case-directory discovery (case-insensitive fnmatch on file names).
DISCOVERY: dict[str, list[str]] = {
    "usnjrnl": ["*$j_output*.csv", "*usnjrnl*.csv", "usn*.csv"],
    "mft": ["*$mft_output*.csv", "mft.csv"],
    "logfile": ["*logfile*.csv"],
    "prefetch": ["*pecmd_output.csv", "prefetch.csv"],
    "amcache": ["*fileentries*.csv", "amcache.csv"],
}

# Scoring thresholds (defaults; tune against labelled data for your environment).
SCORING: dict[str, float] = {
    "detect_threshold": 0.35,     # support needed to list a tool at all
    "attribute_threshold": 0.50,  # support needed to attribute
    "ambiguity_margin": 0.15,     # min support gap between #1 and #2
}

EVIDENCE_CAP = 40  # evidence rows kept per rule in the report


def load_column_overrides(path: Path | None) -> dict[str, dict[str, list[str]]]:
    cols = {k: {kk: list(vv) for kk, vv in v.items()} for k, v in COLUMNS.items()}
    if path is None:
        return cols
    with open(path, encoding="utf-8") as f:
        override: dict[str, Any] = yaml.safe_load(f) or {}
    for artifact, mapping in override.items():
        if artifact not in cols:
            raise ValueError(f"Unknown artifact in column overrides: {artifact}")
        for logical, candidates in mapping.items():
            if isinstance(candidates, str):
                candidates = [candidates]
            # user-supplied names are tried first
            cols[artifact][logical] = list(candidates) + cols[artifact].get(logical, [])
    return cols

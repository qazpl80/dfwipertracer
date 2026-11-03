"""Small helpers shared across the pipeline."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_TS_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?\s*(Z|[+-]\d{2}:?\d{2})?$"
)


def canon_op(op: str) -> str:
    """Canonicalise an operation / reason name to UPPER_SNAKE_CASE.

    'RenameNewName' -> 'RENAME_NEW_NAME'
    'SetBitsInNonresidentBitMap' -> 'SET_BITS_IN_NONRESIDENT_BIT_MAP'
    'FILE_DELETE' -> 'FILE_DELETE'
    """
    op = op.strip()
    if not op:
        return ""
    if "_" in op or op.isupper():
        return op.upper().replace(" ", "_")
    return _CAMEL_BOUNDARY.sub("_", op).upper().replace(" ", "_")


def split_ops(value: str | None) -> frozenset[str]:
    """Split a multi-valued reason field ('RenameNewName|Close') into canonical ops."""
    if not value:
        return frozenset()
    parts = re.split(r"[|,;]", value)
    return frozenset(c for c in (canon_op(p) for p in parts) if c)


def parse_ts(value: str | None) -> datetime | None:
    """Parse the timestamp formats emitted by EZ tools and most CSV parsers.

    Handles 7-digit fractional seconds (MFTECmd) and returns an aware UTC datetime.
    Returns None for blanks or unparseable values (never raises).
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    m = _TS_RE.match(value)
    if not m:
        return None
    y, mo, d, h, mi, s, frac, tz = m.groups()
    micro = int((frac or "0")[:6].ljust(6, "0"))
    try:
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), micro, tzinfo=timezone.utc)
    except ValueError:
        return None
    # EZ tools write UTC; explicit offsets are rare, but honour them if present.
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        tz = tz[1:].replace(":", "")
        dt = dt - sign * timedelta(hours=int(tz[:2]), minutes=int(tz[2:]))
    return dt


def to_int(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    value = value.strip()
    if not value:
        return None
    try:
        return int(value, 16) if value.lower().startswith("0x") else int(float(value))
    except ValueError:
        return None


def to_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    v = value.strip().lower()
    if v in ("true", "1", "yes", "y"):
        return True
    if v in ("false", "0", "no", "n"):
        return False
    return None


def sha256_file(path: Path, chunk: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None

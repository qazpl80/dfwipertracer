from datetime import timezone

import pytest

from wipertrace.util import canon_op, parse_ts, split_ops, to_int


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("RenameNewName", "RENAME_NEW_NAME"),
        ("SetBitsInNonresidentBitMap", "SET_BITS_IN_NONRESIDENT_BIT_MAP"),
        ("InitializeFileRecordSegment", "INITIALIZE_FILE_RECORD_SEGMENT"),
        ("FILE_DELETE", "FILE_DELETE"),
        ("  DataOverwrite ", "DATA_OVERWRITE"),
    ],
)
def test_canon_op(raw, expected):
    assert canon_op(raw) == expected


def test_split_ops():
    assert split_ops("RenameNewName|Close") == {"RENAME_NEW_NAME", "CLOSE"}
    assert split_ops("") == frozenset()


def test_parse_ts_mftecmd_seven_digit_fraction():
    dt = parse_ts("2026-10-14 09:12:33.1234567")
    assert dt.tzinfo == timezone.utc and dt.microsecond == 123456


def test_parse_ts_1601_and_garbage():
    assert parse_ts("1601-01-01 00:00:00.0000000").year == 1601
    assert parse_ts("not a time") is None
    assert parse_ts("") is None


def test_to_int():
    assert to_int("0x1A") == 26 and to_int("42") == 42 and to_int("") is None

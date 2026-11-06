"""$MFT extractor (MFTECmd CSV).

One MFT_RECORD event per row, with derived attributes that signature rules use:
  name_len, size, in_use, parent_path, all_si_ts_1601, all_fn_ts_1601, all_ts_1601,
  any_ts_1601, ts (dict)
"""

from __future__ import annotations

from collections.abc import Iterable

from wipertrace.extractors.base import Extractor
from wipertrace.models import Event, Origin
from wipertrace.util import parse_ts, to_bool, to_int

SI_FIELDS = ("si_created", "si_modified", "si_record_change", "si_accessed")
FN_FIELDS = ("fn_created", "fn_modified", "fn_record_change", "fn_accessed")


class MftExtractor(Extractor):
    artifact = "mft"
    required = ("name", "entry")

    def parse_row(self, row: dict[str, str], origin: Origin) -> Iterable[Event]:
        ts = {f: self.col(row, f) for f in SI_FIELDS + FN_FIELDS}
        si_present = [v for k, v in ts.items() if k.startswith("si_") and v]
        fn_present = [v for k, v in ts.items() if k.startswith("fn_") and v]
        all_present = [v for v in ts.values() if v]
        name = self.col(row, "name")
        yield Event(
            source=self.artifact,
            ops=frozenset({"MFT_RECORD"}),
            timestamp=parse_ts(ts["si_record_change"]),
            name=name,
            file_ref=to_int(self.col(row, "entry")),
            seq=to_int(self.col(row, "seq")),
            parent_ref=to_int(self.col(row, "parent")),
            attrs={
                "parent_path": self.col(row, "parent_path") or "",
                "in_use": to_bool(self.col(row, "in_use")),
                "is_directory": to_bool(self.col(row, "is_directory")),
                "size": to_int(self.col(row, "size")),
                "ts": ts,
                # Eraser sets timestamps to 1601-01-01 (FILETIME ~0); NTFS still rewrites $SI LastRecordChange.
                "all_si_ts_1601": bool(si_present) and all(v.startswith("1601") for v in si_present),
                # Eraser's 1601 times reach $FILE_NAME too; BCWipe sets only $STANDARD_INFORMATION.
                "all_fn_ts_1601": bool(fn_present) and all(v.startswith("1601") for v in fn_present),
                "all_ts_1601": bool(all_present) and all(v.startswith("1601") for v in all_present),
                "any_ts_1601": any(v.startswith("1601") for v in all_present),
            },
            origin=origin,
        )

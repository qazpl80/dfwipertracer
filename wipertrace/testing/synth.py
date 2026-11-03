"""Synthetic case generator.

Writes parser-format CSVs (MFTECmd $J / $MFT, LogFileParser-style $LogFile, PECmd,
AmcacheParser) that reproduce the per-tool behaviours encoded in the signature
database. Used by the test-suite and `wipertrace demo`. Synthetic cases verify
the software's logic; they are not a substitute for real acquisitions when
measuring detection accuracy.
"""

from __future__ import annotations

import csv
import json
import random
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path

T0 = datetime(2026, 10, 14, 9, 0, 0, tzinfo=timezone.utc)
EPOCH_1601 = "1601-01-01 00:00:00.0000000"

USN_HEADER = ["Name", "Extension", "EntryNumber", "SequenceNumber", "ParentEntryNumber",
              "ParentSequenceNumber", "ParentPath", "UpdateSequenceNumber", "UpdateTimestamp",
              "UpdateReasons", "FileAttributes", "OffsetToData", "SourceFile"]
MFT_HEADER = ["EntryNumber", "SequenceNumber", "InUse", "ParentEntryNumber", "ParentSequenceNumber",
              "ParentPath", "FileName", "Extension", "FileSize", "ReferenceCount", "IsDirectory",
              "Created0x10", "Created0x30", "LastModified0x10", "LastModified0x30",
              "LastRecordChange0x10", "LastRecordChange0x30", "LastAccess0x10", "LastAccess0x30",
              "SourceFile"]
LOG_HEADER = ["lf_Offset", "lf_MFTReference", "lf_LSN", "lf_RedoOperation", "lf_UndoOperation", "lf_FileName"]
PF_HEADER = ["SourceFilename", "ExecutableName", "Hash", "RunCount", "LastRun"] + \
            [f"PreviousRun{i}" for i in range(7)] + ["FilesLoaded"]
AM_HEADER = ["ProgramName", "FileId", "FullPath", "Name", "SHA1", "FileKeyLastWriteTimestamp"]

WORDS = ["report", "invoice", "salary", "contract", "notes", "budget", "plan", "design", "minutes", "draft"]
EXTS = ["docx", "xlsx", "pdf", "txt", "pptx"]


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f") + "0"


class CaseBuilder:
    def __init__(self, seed: int = 1, t0: datetime = T0):
        self.rng = random.Random(seed)
        self.t = t0
        self.usn_no, self.lsn, self.entry = 10_000, 50_000, 100_200
        self.usn_rows: list[list] = []
        self.mft_rows: list[list] = []
        self.log_rows: list[list] = []
        self.pf_rows: list[list] = []
        self.am_rows: list[list] = []

    # -- primitives ------------------------------------------------------
    def tick(self, ms: float = 20) -> None:
        self.t += timedelta(milliseconds=ms)

    def new_entry(self) -> int:
        self.entry += 1
        return self.entry

    def usn(self, name, entry, reasons, seq=1, parent=5000, parent_path=".\\Users\\lab\\Corpus", attrs="Archive"):
        self.usn_no += 96
        ext = "." + name.rsplit(".", 1)[1] if "." in name else ""
        self.usn_rows.append([name, ext, entry, seq, parent, 1, parent_path, self.usn_no, _ts(self.t),
                              reasons, attrs, 0, "$J"])
        self.tick(5)

    def log(self, entry, redo, undo="Noop", name=""):
        self.lsn += 88
        self.log_rows.append([self.lsn * 8, entry, self.lsn, redo, undo, name])

    def mft(self, entry, name, size, in_use, parent_path=".\\Users\\lab\\Corpus", seq=1,
            is_dir=False, ts: str | None = None, fn_ts: str | None = None):
        ts = ts or _ts(self.t)
        fn = fn_ts if fn_ts is not None else ts
        ext = "." + name.rsplit(".", 1)[1] if "." in name else ""
        self.mft_rows.append([entry, seq, in_use, 5000, 1, parent_path, name, ext, size, 1, is_dir,
                              ts, fn, ts, fn, ts, fn, ts, fn, "$MFT"])

    def prefetch(self, exe: str, times: list[datetime]):
        times = sorted(times, reverse=True)
        prev = [_ts(t) for t in times[1:8]] + [""] * (7 - len(times[1:8]))
        h = "%08X" % self.rng.getrandbits(32)
        self.pf_rows.append([f"C:\\Windows\\Prefetch\\{exe}-{h}.pf", exe, h, len(times), _ts(times[0])] + prev +
                            ["\\VOLUME{...}\\WINDOWS\\SYSTEM32\\NTDLL.DLL"])

    def rand_name(self, length: int) -> str:
        return "".join(self.rng.choice(string.ascii_letters + string.digits) for _ in range(length))

    # -- scaffolding -----------------------------------------------------
    def corpus(self, n: int) -> list[tuple[int, str, int]]:
        files = []
        for i in range(n):
            name = f"{self.rng.choice(WORDS)}_{i:03d}.{self.rng.choice(EXTS)}"
            e, size = self.new_entry(), self.rng.randint(4_000, 900_000)
            self.usn(name, e, "FileCreate")
            self.usn(name, e, "FileCreate|DataExtend|Close")
            self.log(e, "InitializeFileRecordSegment", "Noop", name)
            files.append((e, name, size))
        self.tick(60_000)
        return files

    def background(self):
        for exe in ("SVCHOST.EXE", "CHROME.EXE", "EXPLORER.EXE", "SEARCHINDEXER.EXE"):
            self.prefetch(exe, [self.t - timedelta(hours=h) for h in (1, 5, 30)])
        for i in range(20):
            e, name = self.new_entry(), f"cache_{i:04d}.tmp"
            self.usn(name, e, "FileCreate", parent_path=".\\Users\\lab\\AppData\\Local\\Temp")
            self.usn(name, e, "DataExtend|Close", parent_path=".\\Users\\lab\\AppData\\Local\\Temp")
            self.log(e, "InitializeFileRecordSegment", "Noop", name)
            for _ in range(3):
                self.log(e, "UpdateResidentValue", "UpdateResidentValue")
            for _ in range(5):
                self.log(e, "SetBitsInNonresidentBitMap", "ClearBitsInNonresidentBitMap")
            self.log(e, "UpdateMappingPairs", "UpdateMappingPairs")
            self.mft(e, name, self.rng.randint(100, 50_000), True,
                     parent_path=".\\Users\\lab\\AppData\\Local\\Temp")

    # -- output ----------------------------------------------------------
    def write(self, out: Path, scenario: str, meta: dict) -> list[Path]:
        out.mkdir(parents=True, exist_ok=True)
        targets = [
            ("synth_MFTECmd_$J_Output.csv", USN_HEADER, self.usn_rows),
            ("synth_MFTECmd_$MFT_Output.csv", MFT_HEADER, self.mft_rows),
            ("LogFile.csv", LOG_HEADER, self.log_rows),
            ("synth_PECmd_Output.csv", PF_HEADER, self.pf_rows),
        ]
        if self.am_rows:
            targets.append(("synth_Amcache_UnassociatedFileEntries.csv", AM_HEADER, self.am_rows))
        written = []
        for fname, header, rows in targets:
            p = out / fname
            with open(p, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(header)
                w.writerows(rows)
            written.append(p)
        gt = out / "ground_truth.json"
        gt.write_text(json.dumps({"scenario": scenario, **meta}, indent=2), encoding="utf-8")
        written.append(gt)
        return written


# --------------------------------------------------------------------------- #
# Scenarios — per-tool behaviour as described in docs/ARCHITECTURE.md §5

def _split(name: str) -> tuple[str, str]:
    return tuple(name.rsplit(".", 1)) if "." in name else (name, "")  # type: ignore[return-value]


def sdelete(b: CaseBuilder, files, hard: bool):
    b.prefetch("SDELETE64.EXE", [b.t + timedelta(seconds=8)])
    for e, name, size in files:
        b.usn(name, e, "DataOverwrite")
        b.usn(name, e, "DataOverwrite|Close")
        base, ext = _split(name)
        current = name
        for i in range(26):
            L = chr(ord("A") + i)
            new = L * len(base) + (f".{L * len(ext)}" if ext else "")
            b.usn(current, e, "RenameOldName")
            b.usn(new, e, "RenameNewName")
            b.usn(new, e, "RenameNewName|Close")
            b.log(e, "CreateAttribute", "DeleteAttribute", new)
            b.log(e, "DeleteAttribute", "CreateAttribute", new)
            current = new
        final = "%024X" % b.rng.getrandbits(96)
        b.usn(current, e, "RenameOldName")
        b.usn(final, e, "RenameNewName", parent=29, parent_path=".\\$Extend\\$Deleted")
        b.log(e, "CreateAttribute", "DeleteAttribute", final)
        b.log(e, "DeleteAttribute", "CreateAttribute", final)
        if not hard:  # final FILE_DELETE only reaches the journal at clean shutdown
            b.usn(final, e, "FileDelete|Close", parent=29, parent_path=".\\$Extend\\$Deleted")
        b.mft(e, final, size, in_use=hard, parent_path=".\\$Extend\\$Deleted")


def cipher(b: CaseBuilder, files, hard: bool):
    for e, name, size in files:  # user deletes normally, then wipes free space
        b.usn(name, e, "FileDelete|Close")
        b.mft(e, name, size, in_use=False)
    b.tick(30_000)
    b.prefetch("CIPHER.EXE", [b.t + timedelta(seconds=5)])
    d = b.new_entry()
    b.usn("EFSTMPWP", d, "FileCreate", parent_path=".", attrs="Directory")
    b.usn("EFSTMPWP", d, "FileCreate|Close", parent_path=".", attrs="Directory")
    for i in range(3):
        fe, fname = b.new_entry(), f"fil{i}.tmp"
        b.usn(fname, fe, "FileCreate", parent=d, parent_path=".\\EFSTMPWP")
        for _ in range(10):
            b.usn(fname, fe, "DataExtend", parent=d, parent_path=".\\EFSTMPWP")
        for _ in range(150):
            b.log(fe, "SetBitsInNonresidentBitMap", "ClearBitsInNonresidentBitMap")
            b.log(fe, "ClearBitsInNonresidentBitMap", "SetBitsInNonresidentBitMap")
        for _ in range(80):
            b.log(fe, "UpdateMappingPairs", "UpdateMappingPairs")
        b.usn(fname, fe, "FileDelete|Close", parent=d, parent_path=".\\EFSTMPWP")
        b.mft(fe, fname, 2_000_000_000, in_use=False, parent_path=".\\EFSTMPWP")
    if not hard:
        b.usn("EFSTMPWP", d, "FileDelete|Close", parent_path=".", attrs="Directory")
    b.mft(d, "EFSTMPWP", 0, in_use=hard, parent_path=".", is_dir=True)


def bleachbit6(b: CaseBuilder, files, hard: bool):
    b.prefetch("BLEACHBIT_CONSOLE.EXE", [b.t + timedelta(seconds=3)])
    for e, name, size in files:
        new = b.rand_name(len(name))
        b.usn(name, e, "DataOverwrite|Close")
        b.usn(name, e, "RenameOldName")
        b.usn(new, e, "RenameNewName")
        b.usn(new, e, "RenameNewName|Close")
        b.usn(new, e, "DataTruncation")
        if not hard:
            b.usn(new, e, "FileDelete|Close")
        b.log(e, "UpdateResidentValue", "UpdateResidentValue")
        b.mft(e, new, 0, in_use=False)


def bleachbit4(b: CaseBuilder, files, hard: bool):
    b.prefetch("BLEACHBIT_CONSOLE.EXE", [b.t + timedelta(seconds=3)])
    for e, name, size in files:
        long_name, short = b.rand_name(226), b.rand_name(1)
        b.usn(name, e, "DataOverwrite|Close")
        b.usn(name, e, "RenameOldName")
        b.usn(long_name, e, "RenameNewName")
        b.usn(long_name, e, "RenameOldName")
        b.usn(short, e, "RenameNewName")
        b.usn(short, e, "RenameNewName|Close")
        if not hard:
            b.usn(short, e, "FileDelete|Close")
        b.log(e, "WriteEndOfFileRecordSegment", "Noop", long_name)
        b.log(e, "InitializeFileRecordSegment", "Noop", short)
        b.mft(e, short, 0, in_use=False)


def eraser(b: CaseBuilder, files, hard: bool):
    run = b.t + timedelta(seconds=4)
    b.prefetch("ERASER.EXE", [run])
    b.am_rows.append(["Eraser", "", "C:\\Program Files\\Eraser\\Eraser.exe", "Eraser.exe", "", _ts(run)])
    for e, name, size in files:
        b.usn(name, e, "DataOverwrite")
        b.usn(name, e, "DataOverwrite|Close")
        current = name
        for _ in range(7):
            new = b.rand_name(b.rng.randint(8, 20))
            b.usn(current, e, "RenameOldName")
            b.usn(new, e, "RenameNewName")
            b.log(e, "CreateAttribute", "DeleteAttribute", new)
            b.log(e, "DeleteAttribute", "CreateAttribute", new)
            current = new
        for k in range(16):
            b.usn(current, e, "BasicInfoChange" if k % 2 == 0 else "BasicInfoChange|Close")
        for _ in range(30):
            b.log(e, "UpdateResidentValue", "UpdateResidentValue")
        if not hard:
            b.usn(current, e, "FileDelete|Close")
        b.mft(e, current, 0, in_use=False, ts=EPOCH_1601, fn_ts=EPOCH_1601)


def explorer_delete(b: CaseBuilder, files, hard: bool):
    for e, name, size in files:  # recycle, then empty the Recycle Bin
        ext = _split(name)[1]
        rname = "$R" + "".join(b.rng.choice(string.ascii_uppercase + string.digits) for _ in range(6)) + f".{ext}"
        b.usn(name, e, "RenameOldName")
        b.usn(rname, e, "RenameNewName", parent_path=".\\$Recycle.Bin\\S-1-5-21")
        b.usn(rname, e, "RenameNewName|Close", parent_path=".\\$Recycle.Bin\\S-1-5-21")
    b.tick(120_000)
    for e, name, size in files:
        b.mft(e, name, size, in_use=False, parent_path=".\\$Recycle.Bin\\S-1-5-21")
        b.usn(name, e, "FileDelete|Close")


def rename_churn(b: CaseBuilder, files, hard: bool):
    for e, name, size in files:
        base, ext = _split(name)
        current = name
        for k in range(b.rng.randint(1, 3)):
            new = f"{base}_final_v{k + 2}.{ext}"
            b.usn(current, e, "RenameOldName")
            b.usn(new, e, "RenameNewName")
            b.usn(new, e, "RenameNewName|Close")
            current = new
        b.mft(e, current, size, in_use=True)


SCENARIOS = {
    "sdelete": ("sdelete", sdelete),
    "cipher": ("cipher", cipher),
    "bleachbit6": ("bleachbit6", bleachbit6),
    "bleachbit4": ("bleachbit4", bleachbit4),
    "eraser": ("eraser", eraser),
    "explorer_delete": (None, explorer_delete),
    "rename_churn": (None, rename_churn),
}


def write_case(scenario: str, out: Path, n_files: int = 10, hard_shutdown: bool = False, seed: int = 1) -> list[Path]:
    label, fn = SCENARIOS[scenario]
    b = CaseBuilder(seed)
    b.background()
    files = b.corpus(n_files)
    fn(b, files, hard_shutdown)
    return b.write(out, scenario, {"expected_tool": label, "files": n_files, "hard_shutdown": hard_shutdown})

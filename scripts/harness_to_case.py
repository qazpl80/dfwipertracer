"""Convert a wipertrace-vmware harness run folder into a WiperTrace case directory.

The harness stores the raw, cold-carved NTFS metadata of the evidence volume
(evidence/$J.gz, $MFT.gz, $LogFile.gz) and the guest's Prefetch folder. WiperTrace
reads parser CSVs, so this script writes CSVs in the same column layout as the
tools an examiner would use:

    $UsnJrnl:$J  -> MFTECmd   -f $J   --csv  layout   (harness_MFTECmd_$J_Output.csv)
    $MFT         -> MFTECmd   -f $MFT --csv  layout   (harness_MFTECmd_$MFT_Output.csv)
    Prefetch     -> PECmd     -d Prefetch --csv       (harness_PECmd_Output.csv)
    $LogFile     -> LogFileParser-style CSV           (harness_LogFile.csv)

The $J, $MFT and Prefetch conversions carry the same fields MFTECmd / PECmd
report. The $LogFile conversion is an approximation of LogFileParser: the MFT
reference is derived from the target VCN of operations on file records, and file
names are taken from $FILE_NAME structures in index-entry and file-record redo
data. Use it for development and regression testing; for casework, parse
$LogFile with LogFileParser.

Amcache.hve is not converted (it needs a registry-hive parser); in the 72 lab
runs only Eraser ever appears in it.

Usage:
    python scripts/harness_to_case.py RUN_DIR OUT_DIR
    python scripts/harness_to_case.py RUNS_ROOT OUT_ROOT --all

Requires: dissect.util (Prefetch decompression) — pip install dissect.util
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import struct
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

FT_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)

REASONS = [  # (bit, MFTECmd name) in MFTECmd's order
    (0x1, "DataOverwrite"), (0x2, "DataExtend"), (0x4, "DataTruncation"),
    (0x10, "NamedDataOverwrite"), (0x20, "NamedDataExtend"), (0x40, "NamedDataTruncation"),
    (0x100, "FileCreate"), (0x200, "FileDelete"), (0x400, "EaChange"),
    (0x800, "SecurityChange"), (0x1000, "RenameOldName"), (0x2000, "RenameNewName"),
    (0x4000, "IndexableChange"), (0x8000, "BasicInfoChange"), (0x10000, "HardLinkChange"),
    (0x20000, "CompressionChange"), (0x40000, "EncryptionChange"), (0x80000, "ObjectIdChange"),
    (0x100000, "ReparsePointChange"), (0x200000, "StreamChange"), (0x400000, "TransactedChange"),
    (0x800000, "IntegrityChange"), (0x80000000, "Close"),
]
FILE_ATTRS = [
    (0x1, "ReadOnly"), (0x2, "Hidden"), (0x4, "System"), (0x10, "Directory"), (0x20, "Archive"),
    (0x80, "Normal"), (0x100, "Temporary"), (0x200, "SparseFile"), (0x400, "ReparsePoint"),
    (0x800, "Compressed"), (0x2000, "NotContentIndexed"), (0x4000, "Encrypted"),
]


def filetime(v: int) -> str:
    """FILETIME -> MFTECmd text ('YYYY-MM-DD HH:MM:SS.fffffff'); 0 -> 1601 epoch."""
    if v is None:
        return ""
    try:
        dt = FT_EPOCH + timedelta(microseconds=v // 10)
    except OverflowError:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{v % 10_000_000:07d}"


def flags_text(value: int, table) -> str:
    return "|".join(name for bit, name in table if value & bit)


# --------------------------------------------------------------------------- $MFT
def _fixup(buf: bytes, sector: int = 512) -> bytes | None:
    usa_off, usa_cnt = struct.unpack_from("<HH", buf, 4)
    b = bytearray(buf)
    usn = b[usa_off:usa_off + 2]
    for k in range(1, usa_cnt):
        end = k * sector - 2
        if end + 2 > len(b):
            break
        if b[end:end + 2] != usn:
            return None
        b[end:end + 2] = b[usa_off + 2 * k:usa_off + 2 * k + 2]
    return bytes(b)


def _fn_content(content: bytes) -> dict:
    parent = struct.unpack_from("<Q", content, 0)[0]
    times = struct.unpack_from("<QQQQ", content, 8)  # created, modified, mft-changed, accessed
    nlen, namespace = content[64], content[65]
    return dict(parent=parent & 0xFFFFFFFFFFFF, parent_seq=parent >> 48, times=times,
                name=content[66:66 + 2 * nlen].decode("utf-16le", "replace"), namespace=namespace)


def parse_mft_record(rec: bytes, idx: int) -> dict | None:
    if rec[:4] != b"FILE":
        return None
    rec = _fixup(rec)
    if rec is None:
        return None
    seq, _, attr_off, flags, used = struct.unpack_from("<HHHHI", rec, 16)
    out = dict(entry=idx, seq=seq, in_use=bool(flags & 1), is_dir=bool(flags & 2),
               si=None, fns=[], size=0, base=struct.unpack_from("<Q", rec, 32)[0] & 0xFFFFFFFFFFFF)
    off = attr_off
    while off + 16 <= min(used, len(rec)):
        atype, alen = struct.unpack_from("<II", rec, off)
        if atype == 0xFFFFFFFF or alen == 0 or off + alen > len(rec):
            break
        nonres, nlen = rec[off + 8], rec[off + 9]
        content = None
        if not nonres:
            clen, coff = struct.unpack_from("<IH", rec, off + 16)
            content = rec[off + coff:off + coff + clen]
        if atype == 0x10 and content and len(content) >= 32:
            out["si"] = struct.unpack_from("<QQQQ", content, 0)
        elif atype == 0x30 and content and len(content) >= 66:
            out["fns"].append(_fn_content(content))
        elif atype == 0x80 and nlen == 0:
            out["size"] = struct.unpack_from("<Q", rec, off + 48)[0] if nonres else len(content or b"")
        off += alen
    return out


def best_fn(fns: list[dict]) -> dict | None:
    """MFTECmd prefers the Win32 (or Win32+DOS) name over the 8.3 DOS name."""
    if not fns:
        return None
    return sorted(fns, key=lambda f: {1: 0, 3: 0, 0: 1, 2: 2}.get(f["namespace"], 3))[0]


def convert_mft(mft: bytes, out: Path) -> dict[int, str]:
    recs: dict[int, dict] = {}
    for idx in range(len(mft) // 1024):
        r = parse_mft_record(mft[idx * 1024:(idx + 1) * 1024], idx)
        if r:
            recs[idx] = r
    names = {i: (best_fn(r["fns"]) or {}) for i, r in recs.items()}
    cache: dict[int, str] = {5: "."}

    def path_of(entry: int, depth: int = 0) -> str:
        if entry in cache:
            return cache[entry]
        fn = names.get(entry) or {}
        if not fn or depth > 64:
            return f".\\PathUnknown\\Directory with ID 0x{entry:08X}"
        parent = path_of(fn["parent"], depth + 1) if fn["parent"] != entry else "."
        cache[entry] = parent + "\\" + fn["name"]
        return cache[entry]

    header = ["EntryNumber", "SequenceNumber", "InUse", "ParentEntryNumber", "ParentSequenceNumber",
              "ParentPath", "FileName", "Extension", "FileSize", "ReferenceCount", "IsDirectory",
              "Created0x10", "Created0x30", "LastModified0x10", "LastModified0x30",
              "LastRecordChange0x10", "LastRecordChange0x30", "LastAccess0x10", "LastAccess0x30",
              "SourceFile"]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for idx, r in recs.items():
            fn = names.get(idx)
            if not fn or r["base"]:          # extension records are folded into their base
                continue
            si = r["si"] or (None,) * 4
            ft = fn["times"]
            name = fn["name"]
            ext = "." + name.rsplit(".", 1)[1] if "." in name else ""
            w.writerow([idx, r["seq"], r["in_use"], fn["parent"], fn["parent_seq"], path_of(fn["parent"]),
                        name, ext, r["size"], len(r["fns"]), r["is_dir"],
                        filetime(si[0]), filetime(ft[0]), filetime(si[1]), filetime(ft[1]),
                        filetime(si[2]), filetime(ft[2]), filetime(si[3]), filetime(ft[3]), "$MFT"])
    return {i: path_of(i) for i in names}


# --------------------------------------------------------------------------- $J
def convert_usn(raw: bytes, out: Path, paths: dict[int, str]) -> int:
    header = ["Name", "Extension", "EntryNumber", "SequenceNumber", "ParentEntryNumber",
              "ParentSequenceNumber", "ParentPath", "UpdateSequenceNumber", "UpdateTimestamp",
              "UpdateReasons", "FileAttributes", "OffsetToData", "SourceFile"]
    n, i, rows = len(raw), 0, 0
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        while i + 60 <= n:
            length = struct.unpack_from("<I", raw, i)[0]
            if length == 0 or struct.unpack_from("<H", raw, i + 4)[0] != 2 or not 60 <= length <= 1024:
                i += 8
                continue
            ref, parent, usn, ft, reason = struct.unpack_from("<QQqqI", raw, i + 8)
            if not 125_911_584_000_000_000 <= ft <= 157_469_184_000_000_000:  # 2000..2100
                i += 8
                continue
            attrs = struct.unpack_from("<I", raw, i + 52)[0]
            nlen, noff = struct.unpack_from("<HH", raw, i + 56)
            name = raw[i + noff:i + noff + nlen].decode("utf-16le", "replace")
            ext = "." + name.rsplit(".", 1)[1] if "." in name else ""
            pref = parent & 0xFFFFFFFFFFFF
            w.writerow([name, ext, ref & 0xFFFFFFFFFFFF, ref >> 48, pref, parent >> 48,
                        paths.get(pref, ""), usn, filetime(ft), flags_text(reason, REASONS),
                        flags_text(attrs, FILE_ATTRS), i, "$J"])
            rows += 1
            i += length
    return rows


# --------------------------------------------------------------------------- $LogFile
PAGE, DATA_START = 4096, 0x40
OPS = ["Noop", "CompensationLogRecord", "InitializeFileRecordSegment", "DeallocateFileRecordSegment",
       "WriteEndOfFileRecordSegment", "CreateAttribute", "DeleteAttribute", "UpdateResidentValue",
       "UpdateNonresidentValue", "UpdateMappingPairs", "DeleteDirtyClusters", "SetNewAttributeSizes",
       "AddIndexEntryRoot", "DeleteIndexEntryRoot", "AddIndexEntryAllocation", "DeleteIndexEntryAllocation",
       "WriteEndOfIndexBuffer", "SetIndexEntryVcnRoot", "SetIndexEntryVcnAllocation", "UpdateFileNameRoot",
       "UpdateFileNameAllocation", "SetBitsInNonresidentBitMap", "ClearBitsInNonresidentBitMap", "HotFix",
       "EndTopLevelAction", "PrepareTransaction", "CommitTransaction", "ForgetTransaction",
       "OpenNonresidentAttribute", "OpenAttributeTableDump", "AttributeNamesDump", "DirtyPageTableDump",
       "TransactionTableDump", "UpdateRecordDataRoot", "UpdateRecordDataAllocation",
       "UpdateRelativeDataInIndex", "UpdateRelativeDataInIndex2", "ZeroEndOfFileRecord"]
# Operations that act on a file record segment: their target VCN locates the MFT entry.
RECORD_OPS = {2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 19, 37}
INDEX_OPS = {12, 13, 14, 15}  # carry an index entry whose key is a $FILE_NAME


def _name_from_index_entry(data: bytes) -> str:
    if len(data) < 0x10 + 66:
        return ""
    key_len = struct.unpack_from("<H", data, 10)[0]
    if key_len < 66:
        return ""
    nlen = data[0x10 + 64]
    raw = data[0x10 + 66:0x10 + 66 + 2 * nlen]
    return raw.decode("utf-16le", "replace") if len(raw) == 2 * nlen else ""


def _name_from_record(data: bytes) -> str:
    if data[:4] != b"FILE":
        return ""
    try:
        r = parse_mft_record(data.ljust(1024, b"\0")[:1024], 0)
    except struct.error:
        return ""
    fn = best_fn(r["fns"]) if r else None
    return fn["name"] if fn else ""


def convert_logfile(raw: bytes, out: Path, cluster: int = 4096, record: int = 1024) -> int:
    size = len(raw)
    pages = {}
    for off in range(0, size, PAGE):
        p = raw[off:off + PAGE]
        if p[:4] == b"RCRD" and (fx := _fixup(p)) is not None:
            pages[off] = fx

    def span(off: int, n: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < n:
            pg = off - off % PAGE
            if pg not in pages:
                return None
            take = min(n - len(buf), PAGE - off % PAGE)
            buf += pages[pg][off % PAGE:off % PAGE + take]
            off = pg + PAGE
            if off >= size:
                off = 2 * PAGE
            off += DATA_START
        return bytes(buf)

    rows, seen = [], set()
    for pg, p in sorted(pages.items()):
        o = DATA_START
        while o + 0x30 <= PAGE:
            lsn = struct.unpack_from("<Q", p, o)[0]
            if lsn and (lsn * 8) % size == pg + o and lsn not in seen:
                clen = struct.unpack_from("<I", p, o + 0x18)[0]
                rtype = struct.unpack_from("<I", p, o + 0x20)[0]
                if rtype in (1, 2) and 0 < clen < 0x10000:
                    data = span(pg + o + 0x30, clen)
                    if data is not None:
                        seen.add(lsn)
                        if rtype == 1 and clen >= 0x20:
                            redo, undo, ro, rl, uo, ul = struct.unpack_from("<6H", data, 0)
                            cluster_index = struct.unpack_from("<H", data, 0x16)[0]
                            vcn = struct.unpack_from("<q", data, 0x18)[0]
                            ref = ""
                            if redo in RECORD_OPS or undo in RECORD_OPS:
                                ref = (vcn * cluster + cluster_index * 512) // record
                            redo_d, undo_d = data[ro:ro + rl], data[uo:uo + ul]
                            name = ""
                            entry_d = redo_d if redo in INDEX_OPS else undo_d if undo in INDEX_OPS else b""
                            if len(entry_d) >= 8:
                                # An index entry names one file; attribute it to that file
                                # (its own MFT reference is the first 8 bytes of the entry).
                                name = _name_from_index_entry(entry_d)
                                if name:
                                    ref = struct.unpack_from("<Q", entry_d, 0)[0] & 0xFFFFFFFFFFFF
                            elif redo == 2:
                                name = _name_from_record(redo_d)
                            rows.append([pg + o, ref, lsn, OPS[redo] if redo < len(OPS) else "Unknown",
                                         OPS[undo] if undo < len(OPS) else "Unknown", name])
                        o += (0x30 + clen + 7) & ~7
                        continue
            o += 8
    rows.sort(key=lambda r: r[2])
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["lf_Offset", "lf_MFTReference", "lf_LSN", "lf_RedoOperation", "lf_UndoOperation",
                    "lf_FileName"])
        w.writerows(rows)
    return len(rows)


# --------------------------------------------------------------------------- Prefetch
def parse_prefetch(path: Path) -> dict | None:
    data = path.read_bytes()
    if data[:3] == b"MAM":
        from dissect.util.compression import lzxpress_huffman

        size = struct.unpack_from("<I", data, 4)[0]
        data = lzxpress_huffman.decompress(data[8:])[:size]
    if data[4:8] != b"SCCA":
        return None
    version = struct.unpack_from("<I", data, 0)[0]
    exe = data[0x10:0x10 + 60].decode("utf-16le", "replace").split("\0")[0]
    pf_hash = struct.unpack_from("<I", data, 0x4C)[0]
    if version >= 26:
        times = [t for t in struct.unpack_from("<8Q", data, 0x80) if t]
        run_count = struct.unpack_from("<I", data, 0xD0)[0]   # v26/v30 (variant 1) layout
    else:
        times = [struct.unpack_from("<Q", data, 0x78)[0]]
        run_count = struct.unpack_from("<I", data, 0x90)[0]
    return dict(exe=exe, hash=f"{pf_hash:08X}", times=times, run_count=run_count)


def convert_prefetch(folder: Path, out: Path) -> int:
    header = ["SourceFilename", "ExecutableName", "Hash", "RunCount", "LastRun"] + \
             [f"PreviousRun{i}" for i in range(7)]
    n = 0
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for pf in sorted(folder.rglob("*.pf")):
            try:
                r = parse_prefetch(pf)
            except Exception as exc:  # corrupt / unsupported file: skip, as PECmd reports an error
                print(f"  prefetch: {pf.name}: {exc}", file=sys.stderr)
                continue
            if not r:
                continue
            t = [filetime(x) for x in r["times"]] + [""] * 8
            w.writerow([f"C:\\Windows\\Prefetch\\{pf.name}", r["exe"], r["hash"], r["run_count"], t[0]] + t[1:8])
            n += 1
    return n


# --------------------------------------------------------------------------- driver
def convert_run(run: Path, out: Path, logfile: bool = True) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    ev = run / "evidence"
    paths = convert_mft(gzip.open(ev / "$MFT.gz").read(), out / "harness_MFTECmd_$MFT_Output.csv")
    stats = {"usn": convert_usn(gzip.open(ev / "$J.gz").read(), out / "harness_MFTECmd_$J_Output.csv", paths)}
    stats["prefetch"] = convert_prefetch(run / "artifacts", out / "harness_PECmd_Output.csv")
    if logfile and (ev / "$LogFile.gz").exists():
        stats["logfile"] = convert_logfile(gzip.open(ev / "$LogFile.gz").read(), out / "harness_LogFile.csv")
    m = json.loads((run / "manifest.json").read_text(encoding="utf-8-sig"))
    (out / "ground_truth.json").write_text(json.dumps({
        "source": "wipertrace-vmware harness", "run_id": m["run_id"], "tool_id": m["tool_id"],
        "mode": m["mode"], "is_control": m["is_control"], "times_utc": m["times_utc"],
        "targets": [t["name"] for t in m["targets"]],
    }, indent=2), encoding="utf-8")
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("src", type=Path, help="harness run folder (or folder of runs with --all)")
    ap.add_argument("out", type=Path, help="case directory to write (or parent folder with --all)")
    ap.add_argument("--all", action="store_true", help="convert every run folder under SRC")
    ap.add_argument("--no-logfile", action="store_true", help="skip the (slow) $LogFile conversion")
    a = ap.parse_args(argv)
    runs = sorted(p.parent for p in a.src.rglob("manifest.json")) if a.all else [a.src]
    for run in runs:
        dest = a.out / run.name if a.all else a.out
        print(run.name, convert_run(run, dest, logfile=not a.no_logfile))
    return 0


if __name__ == "__main__":
    sys.exit(main())

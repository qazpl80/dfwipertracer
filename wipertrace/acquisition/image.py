"""Copy the NTFS artifacts WiperTrace needs out of a raw/E01 image, read-only.

Requires the optional extras:  pip install "wipertrace[image]"  (pytsk3, libewf-python)

Extracted:
  $MFT, $LogFile, $Extend/$UsnJrnl:$J, Windows/Prefetch/*.pf,
  Windows/appcompat/Programs/Amcache.hve (+ .LOG1/.LOG2)
Every extracted file is SHA-256 hashed into acquisition_manifest.json.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from wipertrace.util import sha256_file

log = logging.getLogger("wipertrace.acquisition")
CHUNK = 4 * 1024 * 1024


class AcquisitionError(RuntimeError):
    pass


def _require_tsk():
    try:
        import pytsk3  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise AcquisitionError('pytsk3 is not installed: pip install "wipertrace[image]"') from exc
    return pytsk3


def open_image(path: Path):
    pytsk3 = _require_tsk()
    if path.suffix.lower() in (".e01", ".ex01", ".s01"):
        try:
            import pyewf
        except ImportError as exc:  # pragma: no cover
            raise AcquisitionError('E01 support needs libewf-python: pip install "wipertrace[image]"') from exc

        class EwfImgInfo(pytsk3.Img_Info):
            def __init__(self, handle):
                self._h = handle
                super().__init__(url="", type=pytsk3.TSK_IMG_TYPE_EXTERNAL)

            def close(self):
                self._h.close()

            def read(self, offset, size):
                self._h.seek(offset)
                return self._h.read(size)

            def get_size(self):
                return self._h.get_media_size()

        handle = pyewf.handle()
        handle.open(pyewf.glob(str(path)))
        return EwfImgInfo(handle)
    return pytsk3.Img_Info(str(path))


def ntfs_volumes(img) -> list[tuple[int, object]]:
    """Return (byte_offset, FS_Info) for each NTFS file system in the image."""
    pytsk3 = _require_tsk()
    found = []
    try:
        vs = pytsk3.Volume_Info(img)
        block = vs.info.block_size
        offsets = [p.start * block for p in vs if p.flags == pytsk3.TSK_VS_PART_FLAG_ALLOC and p.len > 2048]
    except OSError:
        offsets = [0]  # no partition table: a bare volume image
    for off in offsets:
        try:
            fs = pytsk3.FS_Info(img, offset=off)
        except OSError:
            continue
        if fs.info.ftype in (pytsk3.TSK_FS_TYPE_NTFS, pytsk3.TSK_FS_TYPE_NTFS_DETECT):
            found.append((off, fs))
    return found


def _copy_stream(fs, inner_path: str, dest: Path, stream: str | None = None, trim_leading_zeros=False) -> bool:
    pytsk3 = _require_tsk()
    try:
        f = fs.open(inner_path)
    except OSError:
        return False
    for attr in f:
        if attr.info.type != pytsk3.TSK_FS_ATTR_TYPE_NTFS_DATA:
            continue
        name = attr.info.name.decode("utf-8", "replace") if attr.info.name else ""
        if (stream or "") != name:
            continue
        size = attr.info.size
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as out:
            offset, started = 0, not trim_leading_zeros
            while offset < size:
                data = f.read_random(offset, min(CHUNK, size - offset), attr.info.type, attr.info.id)
                if not data:
                    break
                # $J is sparse: GBs of leading zeros. USN records are self-describing,
                # so MFTECmd parses a trimmed copy correctly.
                if not started:
                    if not data.strip(b"\x00"):
                        offset += len(data)
                        continue
                    started = True
                out.write(data)
                offset += len(data)
        return True
    return False


def extract_artifacts(image: Path, out_dir: Path, volume_index: int = 0) -> dict:
    img = open_image(image)
    vols = ntfs_volumes(img)
    if not vols:
        raise AcquisitionError("No NTFS volume found in image")
    if volume_index >= len(vols):
        raise AcquisitionError(f"Only {len(vols)} NTFS volume(s) found")
    offset, fs = vols[volume_index]
    raw = out_dir / "raw"
    copied: list[dict] = []

    def record(kind: str, dest: Path):
        copied.append({"artifact": kind, "path": str(dest), "sha256": sha256_file(dest), "size": dest.stat().st_size})

    targets = [
        ("mft", "/$MFT", raw / "$MFT", None, False),
        ("logfile", "/$LogFile", raw / "$LogFile", None, False),
        ("usnjrnl", "/$Extend/$UsnJrnl", raw / "$J", "$J", True),
        ("amcache", "/Windows/appcompat/Programs/Amcache.hve", raw / "Amcache" / "Amcache.hve", None, False),
        ("amcache", "/Windows/appcompat/Programs/Amcache.hve.LOG1", raw / "Amcache" / "Amcache.hve.LOG1", None, False),
        ("amcache", "/Windows/appcompat/Programs/Amcache.hve.LOG2", raw / "Amcache" / "Amcache.hve.LOG2", None, False),
    ]
    for kind, inner, dest, stream, trim in targets:
        if _copy_stream(fs, inner, dest, stream, trim):
            record(kind, dest)
        else:
            log.warning("not found in image: %s%s", inner, f":{stream}" if stream else "")

    try:
        pf_dir = fs.open_dir("/Windows/Prefetch")
        for entry in pf_dir:
            name = entry.info.name.name.decode("utf-8", "replace")
            if name.lower().endswith(".pf"):
                dest = raw / "Prefetch" / name
                if _copy_stream(fs, f"/Windows/Prefetch/{name}", dest):
                    record("prefetch", dest)
    except OSError:
        others = [str(i) for i in range(len(vols)) if i != volume_index]
        hint = f" Image has {len(vols)} NTFS volumes; try --volume {' / '.join(others)}." if others else ""
        log.warning("Prefetch folder not found on NTFS volume %d (disabled, or not the Windows volume?).%s",
                    volume_index, hint)

    manifest = {
        "image": str(image),
        "image_sha256": sha256_file(image) if image.stat().st_size < 64 * 1024**3 else "skipped (>64 GiB)",
        "volume_offset": offset,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "files": copied,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "acquisition_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest

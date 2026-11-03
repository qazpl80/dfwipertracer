"""Run Eric Zimmerman's parsers over extracted artifacts to produce the case CSVs.

Paths may point at .exe (Windows) or .dll (run through `dotnet` on Linux/macOS).
$LogFile has no EZ tool: run your $LogFile parser separately and save the CSV in
the case directory with 'logfile' in its name.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("wipertrace.acquisition")


def _cmd(tool: Path) -> list[str]:
    if tool.suffix.lower() == ".dll":
        dotnet = shutil.which("dotnet")
        if not dotnet:
            raise FileNotFoundError("dotnet runtime not found (needed for .dll EZ tools)")
        return [dotnet, str(tool)]
    return [str(tool)]


def _run(args: list[str]) -> None:
    log.info("running: %s", " ".join(args))
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{Path(args[-1]).name} failed ({proc.returncode}):\n{proc.stderr[-2000:]}")


def run_eztools(
    raw_dir: Path,
    case_dir: Path,
    mftecmd: Path | None = None,
    pecmd: Path | None = None,
    amcacheparser: Path | None = None,
) -> list[str]:
    """Produce mft / $J / prefetch / amcache CSVs in `case_dir`. Returns notes."""
    notes: list[str] = []
    case_dir.mkdir(parents=True, exist_ok=True)
    mft, j = raw_dir / "$MFT", raw_dir / "$J"

    if mftecmd:
        if mft.exists():
            _run(_cmd(mftecmd) + ["-f", str(mft), "--csv", str(case_dir), "--csvf", "mftecmd_$MFT_Output.csv"])
        if j.exists():
            args = ["-f", str(j), "--csv", str(case_dir), "--csvf", "mftecmd_$J_Output.csv"]
            if mft.exists():
                args += ["-m", str(mft)]  # resolve parent paths
            _run(_cmd(mftecmd) + args)
    else:
        notes.append("MFTECmd not given: $MFT and $J were extracted but not parsed.")

    pf = raw_dir / "Prefetch"
    if pecmd and pf.is_dir():
        _run(_cmd(pecmd) + ["-d", str(pf), "--csv", str(case_dir), "--csvf", "pecmd_PECmd_Output.csv"])
    elif not pecmd:
        notes.append("PECmd not given: Prefetch was extracted but not parsed.")

    hve = raw_dir / "Amcache" / "Amcache.hve"
    if amcacheparser and hve.exists():
        _run(_cmd(amcacheparser) + ["-f", str(hve), "--csv", str(case_dir), "--csvf", "amcache.csv", "-i"])
    elif not amcacheparser:
        notes.append("AmcacheParser not given: Amcache was extracted but not parsed.")

    if (raw_dir / "$LogFile").exists():
        notes.append("$LogFile extracted to raw/$LogFile — parse it and save the CSV as <case>/logfile.csv.")
    return notes

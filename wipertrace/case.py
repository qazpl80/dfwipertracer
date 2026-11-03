"""Case handling: locate parser outputs and build a hashed input manifest."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from wipertrace import ARTIFACTS
from wipertrace.config import DISCOVERY
from wipertrace.util import sha256_file


@dataclass
class Case:
    root: Path | None
    inputs: dict[str, list[Path]] = field(default_factory=dict)
    name: str = ""

    @classmethod
    def discover(
        cls,
        root: Path | None,
        explicit: dict[str, list[Path]] | None = None,
        drop: list[str] | None = None,
    ) -> "Case":
        """Find parser CSVs in `root`; explicit paths override discovery.

        `drop` removes artifact classes entirely (used for ablation experiments).
        """
        inputs: dict[str, list[Path]] = {a: [] for a in ARTIFACTS}
        if root is not None:
            if not root.is_dir():
                raise FileNotFoundError(f"Case directory not found: {root}")
            files = sorted(p for p in root.rglob("*.csv") if p.is_file())
            claimed: set[Path] = set()
            for artifact in ARTIFACTS:
                for pattern in DISCOVERY[artifact]:
                    for p in files:
                        if p in claimed:
                            continue
                        if fnmatch.fnmatch(p.name.lower(), pattern):
                            # PECmd also writes a *_Timeline.csv; skip it.
                            if artifact == "prefetch" and "timeline" in p.name.lower():
                                continue
                            inputs[artifact].append(p)
                            claimed.add(p)
        for artifact, paths in (explicit or {}).items():
            if paths:
                inputs[artifact] = [Path(p) for p in paths]
        for artifact in drop or []:
            inputs[artifact] = []
        name = root.name if root else "ad-hoc"
        return cls(root=root, inputs=inputs, name=name)

    def manifest(self) -> list[dict]:
        """SHA-256 every input so the report can prove what was analysed."""
        out = []
        for artifact, paths in self.inputs.items():
            for p in paths:
                st = p.stat()
                out.append(
                    {
                        "artifact": artifact,
                        "path": str(p),
                        "size": st.st_size,
                        "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
                        "sha256": sha256_file(p),
                    }
                )
        return out

    def describe(self) -> dict:
        return {
            "name": self.name,
            "root": str(self.root) if self.root else None,
            "analysed_at": datetime.now(timezone.utc).isoformat(),
            "host": os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", ""),
        }

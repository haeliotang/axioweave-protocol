#!/usr/bin/env python3
"""Verify the published AW-001 Stage 1 file manifest without running a server."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "MANIFEST.sha256"


def main() -> None:
    if not MANIFEST.is_file():
        raise SystemExit("MANIFEST.sha256 missing")
    checked = 0
    for line in MANIFEST.read_text().splitlines():
        digest, _, raw_path = line.partition("  ")
        if not raw_path or len(digest) != 64:
            raise SystemExit(f"bad manifest line: {line!r}")
        path = ROOT / raw_path
        if not path.is_file():
            raise SystemExit(f"missing file: {raw_path}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest:
            raise SystemExit(f"digest mismatch: {raw_path}")
        checked += 1
    runtime = [p for p in ROOT.rglob("*") if p.is_file() and (p.name == "Cargo.toml" or p.suffix == ".rs")]
    if runtime:
        raise SystemExit(f"runtime files unexpectedly present: {runtime[:3]}")
    print(f"snapshot OK: {checked} manifest files; no runtime files")


if __name__ == "__main__":
    main()

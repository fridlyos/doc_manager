"""SHA-256 checksums for a backup set (TECHSTACK 11.5, Phase 8.c).

Streams file hashing (no whole-file loads), writes a ``SHA256SUMS`` file in the
``<sha256>  <relative-path>`` format, and verifies a set against it. Verification
is what a restore/integrity check runs before trusting a backup.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

SUMS_FILENAME = "SHA256SUMS"
_BLOCK = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(_BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def write_sums(directory: Path, filenames: list[str]) -> Path:
    """Write ``SHA256SUMS`` for the given files (relative to ``directory``)."""
    lines = [f"{sha256_file(directory / name)}  {name}" for name in sorted(filenames)]
    sums_path = directory / SUMS_FILENAME
    sums_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sums_path


@dataclass(frozen=True, slots=True)
class VerifyResult:
    ok: bool
    checked: int
    mismatches: list[str]
    missing: list[str]


def verify_sums(directory: Path) -> VerifyResult:
    """Recompute and compare every file listed in ``SHA256SUMS``."""
    sums_path = directory / SUMS_FILENAME
    if not sums_path.is_file():
        return VerifyResult(ok=False, checked=0, mismatches=[], missing=[SUMS_FILENAME])
    mismatches: list[str] = []
    missing: list[str] = []
    checked = 0
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        expected, _, name = line.partition("  ")
        target = directory / name
        if not target.is_file():
            missing.append(name)
            continue
        checked += 1
        if sha256_file(target) != expected:
            mismatches.append(name)
    return VerifyResult(
        ok=not mismatches and not missing, checked=checked, mismatches=mismatches, missing=missing
    )

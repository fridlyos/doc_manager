"""Content hashing for the scanner.

SHA-256 is the content authority for reconciliation: it distinguishes a real
edit from an mtime-only touch, and lets a moved/copied/restored file be
recognized by its bytes rather than its path (TECHSTACK sections 7.1, 7.6).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: Streamed in 1 MiB blocks so a large file never loads into memory at once.
_CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path, *, chunk_size: int = _CHUNK_SIZE) -> str:
    """Return the hex SHA-256 of a file, streaming it in bounded chunks.

    Raises OSError if the file cannot be read (vanished, locked, permission).
    Callers treat that as "not observed this scan" rather than failing the run.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()

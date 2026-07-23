"""Extraction-profile identity.

The extraction profile is the set of settings that affect how a file is turned
into page/section records — currently just the extractor's name and version plus
any per-extractor options. Its hash is part of a content object's reuse key, so a
change to the extractor or its settings yields a distinct artifact rather than
silently reusing content produced by different logic (TECHSTACK sections 5.4, 6).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def extraction_profile_hash(
    extractor_name: str, extractor_version: str, settings: dict[str, Any] | None = None
) -> str:
    canonical = json.dumps(
        {"name": extractor_name, "version": extractor_version, "settings": settings or {}},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

"""Display-path composition (API contract sec. 5).

The only path an API response may expose is ``display_path`` — the location's
``display_root`` joined to a catalog entry's stored (posix) relative path, rendered
with the separators of the location's declared path style. It never exposes the
worker-visible ``scan_root``. Shared by the document serializer and the search
retrieval layer so both render paths identically.
"""

from __future__ import annotations

from doc_manager.domain.enums import PathStyle

_WINDOWS_STYLES = frozenset(
    {PathStyle.windows.value, PathStyle.mapped_drive.value, PathStyle.unc.value}
)


def display_path(path_style: str, display_root: str, relative_path: str) -> str:
    """Join a display root and a stored posix relative path for one path style."""
    if path_style in _WINDOWS_STYLES:
        rel = relative_path.replace("/", "\\")
        root = display_root.rstrip("\\/")
        return f"{root}\\{rel}" if rel else root
    root = display_root.rstrip("/")
    return f"{root}/{relative_path}" if relative_path else root

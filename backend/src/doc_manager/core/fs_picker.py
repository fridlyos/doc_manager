"""Native OS folder-picker support.

A browser cannot hand the backend an absolute filesystem path (the sandbox
hides it), so "select through Windows Explorer" is implemented server-side: the
API process opens the host's native folder dialog and returns the chosen path.

This only works when the API runs somewhere with a GUI-capable Windows host
reachable from the process — native Windows, or WSL with Windows interop
(``powershell.exe`` on PATH). In a headless Linux container it is unavailable,
and callers fall back to the in-app directory browser.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess

from doc_manager.core.logging import get_logger

log = get_logger("doc_manager.fs_picker")

_UNC = re.compile(r"^\\\\")
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")

# Opens the classic Explorer-style folder dialog and writes the chosen path to
# stdout (empty on cancel). -STA is required for Windows Forms dialogs.
_PS_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms; "
    "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
    "$d.Description = 'Select a source folder for Document Manager'; "
    "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
    "{ [Console]::Out.Write($d.SelectedPath) }"
)


def _powershell() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("powershell")


def native_picker_available() -> bool:
    """True when a native folder dialog can actually be shown from here."""
    if platform.system() == "Windows":
        return _powershell() is not None
    # WSL with Windows interop can launch powershell.exe on the Windows host.
    return shutil.which("powershell.exe") is not None


def detect_path_style(path: str) -> str:
    """Mirror the frontend detectPathStyle: shape of the string, not host OS."""
    if _UNC.match(path):
        return "unc"
    if _DRIVE.match(path):
        return "mapped_drive"
    return "linux"


def pick_folder_native(*, timeout: float = 180.0) -> str | None:
    """Show the native folder dialog; return the selected path or None (cancel).

    Blocking — run in a worker thread. Raises RuntimeError if no picker backend
    is available (callers should check native_picker_available first).
    """
    powershell = _powershell()
    if powershell is None:
        raise RuntimeError("no native folder-picker backend on this host")
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-STA", "-Command", _PS_SCRIPT],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.warning("native_picker_timeout")
        return None
    path = completed.stdout.strip()
    return path or None

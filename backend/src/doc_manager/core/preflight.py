"""Storage and mount preflight checks.

These run before a scan or backup so an accidentally-empty local directory is
never reconciled against the catalog and a network-backed filesystem is never
used for live database data (TECHSTACK sections 11.2-11.3). They are pure and
side-effect-scoped so they can be unit-tested with temporary directories.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

# Filesystem types rejected for live PostgreSQL / Qdrant data. SMB/CIFS and NFS
# do not provide the semantics these databases require.
_NETWORK_FS_TYPES = frozenset({"cifs", "smbfs", "smb3", "nfs", "nfs4", "fuse.cifs"})


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str

    @classmethod
    def passed(cls, name: str, detail: str = "") -> CheckResult:
        return cls(name=name, ok=True, detail=detail)

    @classmethod
    def failed(cls, name: str, detail: str) -> CheckResult:
        return cls(name=name, ok=False, detail=detail)


def check_source_sentinel(
    scan_root: Path, expected_location_id: str, sentinel_name: str
) -> CheckResult:
    """A configured source root must carry the expected sentinel id.

    Missing/mismatched sentinel means the mapped drive is disconnected or points
    at the wrong share; the caller must mark the location unavailable rather than
    treat an empty directory as "all files deleted".
    """
    name = "source_sentinel"
    if not scan_root.exists() or not scan_root.is_dir():
        return CheckResult.failed(name, f"scan root not present: {scan_root}")
    sentinel = scan_root / sentinel_name
    if not sentinel.is_file():
        return CheckResult.failed(name, f"sentinel missing: {sentinel}")
    observed = sentinel.read_text(encoding="utf-8").strip()
    if observed != expected_location_id:
        return CheckResult.failed(
            name, "sentinel id mismatch (mount points at an unexpected share)"
        )
    return CheckResult.passed(name, f"sentinel matches {expected_location_id}")


def check_source_read_only(scan_root: Path) -> CheckResult:
    """A source mount must reject writes from the worker."""
    name = "source_read_only"
    if not scan_root.is_dir():
        return CheckResult.failed(name, f"scan root not present: {scan_root}")
    probe = scan_root / f".docman-write-probe-{uuid4().hex}"
    try:
        probe.write_text("probe", encoding="utf-8")
    except OSError:
        return CheckResult.passed(name, "writes correctly rejected")
    # Write unexpectedly succeeded: clean up and fail the check.
    probe.unlink(missing_ok=True)
    return CheckResult.failed(name, "source mount is writable; expected read-only")


def check_backup_probe(backup_root: Path) -> CheckResult:
    """The backup destination must support write, read-back, and delete."""
    name = "backup_write_probe"
    if not backup_root.is_dir():
        return CheckResult.failed(name, f"backup root not present: {backup_root}")
    probe = backup_root / f".docman-backup-probe-{uuid4().hex}"
    token = uuid4().hex
    try:
        probe.write_text(token, encoding="utf-8")
        if probe.read_text(encoding="utf-8") != token:
            return CheckResult.failed(name, "read-back mismatch")
    except OSError as exc:
        return CheckResult.failed(name, f"probe failed: {exc.strerror or exc}")
    finally:
        probe.unlink(missing_ok=True)
    return CheckResult.passed(name, "write/read/delete succeeded")


def filesystem_type(path: Path, *, mounts_source: Path = Path("/proc/mounts")) -> str | None:
    """Best-effort fstype for ``path`` from /proc/mounts (longest mountpoint wins)."""
    try:
        entries = mounts_source.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    target = str(path.resolve())
    best: tuple[int, str] | None = None
    for line in entries:
        fields = line.split()
        if len(fields) < 3:
            continue
        mountpoint, fstype = fields[1], fields[2]
        if target == mountpoint or target.startswith(mountpoint.rstrip("/") + "/"):
            depth = len(mountpoint.rstrip("/"))
            if best is None or depth > best[0]:
                best = (depth, fstype)
    return best[1] if best else None


def check_live_storage_local(path: Path, *, role: str) -> CheckResult:
    """Reject a network-backed filesystem for live PostgreSQL/Qdrant data.

    Qdrant enforces its own startup check; this is an additional deployment
    preflight and, per TECHSTACK 11.2, must not be bypassed for production.
    """
    name = f"live_storage_{role}"
    fstype = filesystem_type(path)
    if fstype is None:
        return CheckResult.passed(name, "fstype undetermined; verify manually")
    if fstype.lower() in _NETWORK_FS_TYPES:
        return CheckResult.failed(
            name, f"{role} data on network filesystem '{fstype}' is not supported"
        )
    return CheckResult.passed(name, f"local filesystem '{fstype}'")


def all_ok(results: list[CheckResult]) -> bool:
    return all(r.ok for r in results)

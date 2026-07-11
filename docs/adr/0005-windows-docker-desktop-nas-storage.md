# ADR 0005: Use Windows Docker Desktop with Local Database Volumes and NAS File Mounts

- **Status:** Proposed
- **Date:** 2026-07-11
- **Decision owners:** Project maintainers

## Context

The runtime host is Windows. The NAS can be attached as a mapped Windows SMB drive, but Docker runs on the Windows machine rather than on the NAS. PostgreSQL and Qdrant perform frequent database/WAL/index writes. Qdrant requires block-level POSIX-compatible live storage and warns against Windows/WSL shared mounts for its data directory.

## Decision

Use Docker Desktop with its WSL 2 Linux engine and Linux containers for PostgreSQL, Qdrant, API, worker, UI build/runtime, and backup maintenance.

### Storage placement

- PostgreSQL `PGDATA`: Docker-managed Linux named volume on local SSD.
- Qdrant `/qdrant/storage`: Docker-managed Linux named volume on local SSD.
- Original documents: mapped NAS path mounted read-only into the worker.
- Extracted artifacts: local named volume by default; optional mapped NAS path because artifacts are immutable/checksummed.
- Completed PostgreSQL/Qdrant-native backup sets: mapped NAS backup path, written only by the maintenance service.
- Backup staging and Qdrant snapshot temporary work: local named volume.
- Ollama: optional native Windows process reached through `host.docker.internal`.
- OpenAI: optional TLS egress from the API container after policy gates.

Never place live PostgreSQL or Qdrant directories on the Windows mapped SMB drive, and never move Docker Desktop's backing disk image to that share as a workaround.

### Mount safety

- Compose uses long-form binds with `create_host_path: false`.
- A source sentinel and expected UNC identity distinguish a real NAS mount from an empty/local directory.
- A failed/unavailable source scan never tombstones unseen documents.
- The backup destination must pass a scoped write/read/delete test.
- Installation includes a Windows reboot/Docker restart visibility test.
- Returned citations use stable UNC display roots rather than assuming a user's drive letter.

### Recovery

Application-aware PostgreSQL dumps/base backups and Qdrant snapshots are copied to immutable completed NAS backup sets. NAS automation copies those sets to a separate destination. A raw copy of changing Docker/database files is not the canonical backup.

## Consequences

### Positive

- Database files receive Linux filesystem semantics and local random-I/O performance.
- Documents and recovery sets benefit from NAS capacity/external backup.
- A NAS outage affects scans/backups without corrupting live database storage.

### Negative

- Live catalog/vector state consumes Windows-host SSD capacity.
- Host loss can lose changes since the last completed backup.
- Windows drive mappings and credentials require preflight and post-reboot tests.
- Hashing large SMB corpora can be network-bound.

## Alternatives considered

- **Live database bind mounts on the mapped SMB drive:** rejected for Qdrant compatibility, durability, and performance.
- **Docker Engine on the NAS:** explicitly outside the selected deployment.
- **Dedicated Linux host/VM with an exclusive NAS iSCSI LUN:** technically valid if live database bytes must reside on NAS hardware, but changes the selected runtime platform.
- **Run all data in ephemeral container layers:** unrecoverable and incompatible with upgrades/restarts.


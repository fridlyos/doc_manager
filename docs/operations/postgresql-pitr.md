# PostgreSQL point-in-time recovery (optional)

**Default stance: not enabled.** The MVP recovery authority is the nightly
application-aware logical dump (`pg_dump --format=custom`, see
[`backup-restore.md`](backup-restore.md)). This document records the trade-off so
an operator who needs a tighter recovery point can enable WAL-archiving PITR
deliberately — it is not wired into the default stack.

## What the default gives you

A nightly logical dump means the recovery point objective (RPO) is **up to 24
hours**: a crash just before the next dump can lose a day of catalog changes.
Because source documents are read-only and the catalog is reconstructable by
re-scanning, the real loss in that window is scan/index/ask bookkeeping and job
state, not primary data. For a single-user local/NAS deployment this is usually
an acceptable RPO, which is why PITR is off by default.

## What PITR would add

Continuous WAL (write-ahead log) archiving plus a periodic base backup lets you
restore to **any moment** between the base backup and the last archived segment —
an RPO of seconds to minutes instead of up to a day. You restore the base backup,
then replay WAL up to a chosen `recovery_target_time`.

## Why it is not the default

- **Operational weight.** It requires a base-backup schedule (`pg_basebackup`), a
  durable WAL archive with its own retention and monitoring, and
  `archive_command`/`restore_command` configuration. A stalled or full archive
  can block PostgreSQL from writing — a worse failure mode than a missed nightly
  dump for this workload.
- **Storage.** WAL accumulates continuously; the archive needs capacity planning
  and pruning independent of the GFS logical-dump retention.
- **The NAS boundary.** PITR is most useful streaming WAL to separate storage.
  The project rule (§11.3) is that live database internals never touch the mapped
  NAS drive, so a WAL archive would need its own carefully-scoped target, not the
  existing `backup` NAS bind.
- **Marginal benefit here.** The catalog rebuilds from read-only sources; the
  vector index rebuilds from the catalog. A sub-day RPO rarely changes the
  recovery outcome for this system.

## If you choose to enable it (sketch, not supported config)

This is a direction, not a drop-in. Validate on your own hardware before relying
on it.

1. Set `wal_level = replica` (or higher), `archive_mode = on`, and an
   `archive_command` that copies each completed segment to a durable archive
   **off** the live `postgres_data` volume and **off** the mapped NAS drive.
2. Take a periodic base backup (`pg_basebackup`) into the same archive scheme.
3. Monitor the archive: a failing `archive_command` must alert, because WAL will
   otherwise pile up in `pg_wal` until the volume fills.
4. To recover: restore the base backup into an empty `postgres_data` volume, set
   `restore_command` and a `recovery_target_time`, and let PostgreSQL replay.
5. After PITR restore, **still run the index rebuild + known-query validation**
   from [`backup-restore.md`](backup-restore.md) — PITR recovers PostgreSQL, not
   Qdrant.

## Recommendation

Keep PITR off for the MVP. Rely on the nightly logical dump and keep the GFS
retention healthy. Revisit PITR only if a concrete requirement for a sub-day RPO
appears, and treat enabling it as its own hardening task with its own testing.

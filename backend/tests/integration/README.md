# Integration tests

Tests that require live PostgreSQL and Qdrant (via the Compose test profile or
Testcontainers) land here starting in Phase 2. They must use synthetic data only
and never real provider credentials (TECHSTACK section 13).

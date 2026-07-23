"""Deployment configuration.

All values are loaded from ``DOCMAN_``-prefixed environment variables (see
``.env.example``). User-manageable data — source locations, schedules — lives in
PostgreSQL, not here. Secrets are read from files (Docker secrets) and never
logged or exposed to the UI.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    development = "development"
    production = "production"
    test = "test"


class GenerationProvider(StrEnum):
    ollama = "ollama"
    openai = "openai"


class FilesystemProfile(StrEnum):
    auto = "auto"
    windows = "windows"
    unix = "unix"


class Settings(BaseSettings):
    """Deployment-level settings.

    Startup fails loudly on an invalid combination rather than silently
    degrading (for example, selecting the OpenAI provider while external
    processing is disabled).
    """

    model_config = SettingsConfigDict(
        env_prefix="DOCMAN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: Environment = Environment.development

    # --- Network binding. Default localhost-only; LAN binding is deliberate. ---
    bind_host: str = "127.0.0.1"
    port: int = 8000

    # --- Data services ---
    database_url: str = "postgresql+psycopg://docman:docman@postgres:5432/docman"
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "doc_chunks"

    # --- Generation providers ---
    generation_provider: GenerationProvider = GenerationProvider.ollama
    external_llm_enabled: bool = False
    external_provider_allowlist: str = "openai"
    external_source_default: str = "deny"
    external_max_evidence_tokens: int = 12_000
    external_max_output_tokens: int = 2_000
    external_request_timeout_seconds: int = 90
    # Local generation: max tokens to generate and the overall per-request
    # deadline. The external path uses external_* above.
    generation_max_output_tokens: int = 1_200
    generation_request_timeout_seconds: float = 120.0
    # SSE keep-alive comment interval for Ask streaming (contract §8.3: ≥ every 15s).
    sse_keepalive_seconds: float = 15.0

    ollama_url: str = "http://host.docker.internal:11434"
    ollama_chat_model: str = "llama3.1:8b"
    # Context window (tokens) advertised for the local model; drives evidence
    # budgeting and is passed to Ollama as options.num_ctx.
    ollama_num_ctx: int = 8192

    openai_model: str | None = None
    openai_api_key_file: Path | None = None

    # --- Embeddings + artifacts ---
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_batch_size: int = 256
    artifact_root: Path = Path("/app-data/extracted-text")
    frontend_dist: Path = Path("/app/frontend-dist")
    allowed_source_roots: str = "/sources"

    # --- NAS / mounts ---
    nas_documents_host_path: str | None = None
    nas_artifacts_host_path: str | None = None
    nas_backups_host_path: str | None = None
    nas_mount_sentinel: str = ".docman-source-id"
    # Filesystem profile drives default path style and the folder-picker mode.
    # "auto" resolves from the host OS at runtime (see resolved_filesystem_profile).
    filesystem_profile: FilesystemProfile = FilesystemProfile.auto

    # --- Backup ---
    backup_root: Path = Path("/backups")
    backup_retention_daily: int = 14
    backup_retention_weekly: int = 8
    backup_retention_monthly: int = 12

    # --- Runtime ---
    log_level: str = "INFO"
    worker_concurrency: int = 1
    # Lease/heartbeat defaults per the job state-machine contract (sec. 5):
    # 90 s lease, heartbeat at most every 20 s (must stay under lease/3).
    job_lease_seconds: int = 90
    job_heartbeat_seconds: int = 20
    job_poll_interval_seconds: float = 2.0
    job_max_attempts: int = 3
    job_retry_base_delay_seconds: float = 5.0
    job_retry_max_delay_seconds: float = 900.0
    reaper_interval_seconds: float = 15.0
    scheduler_interval_seconds: float = 60.0
    worker_shutdown_grace_seconds: float = 25.0
    chunk_target_tokens: int = 750
    chunk_overlap_tokens: int = 100
    search_top_k: int = 12
    search_score_threshold: float | None = None
    store_query_history: bool = False

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        if not value.startswith("postgresql"):
            raise ValueError("database_url must be a postgresql connection URL")
        return value

    @field_validator("external_source_default")
    @classmethod
    def _validate_source_default(cls, value: str) -> str:
        if value not in {"deny", "allow"}:
            raise ValueError("external_source_default must be 'deny' or 'allow'")
        return value

    @property
    def allowed_source_root_paths(self) -> list[Path]:
        return [Path(p.strip()) for p in self.allowed_source_roots.split(",") if p.strip()]

    @property
    def resolved_filesystem_profile(self) -> str:
        """ "windows" or "unix" — resolves "auto" from the host OS at runtime."""
        if self.filesystem_profile is not FilesystemProfile.auto:
            return self.filesystem_profile.value
        import platform

        return "windows" if platform.system() == "Windows" else "unix"

    @property
    def external_provider_allowlist_set(self) -> set[str]:
        return {p.strip() for p in self.external_provider_allowlist.split(",") if p.strip()}

    @model_validator(mode="after")
    def _validate_provider_consistency(self) -> Settings:
        if self.generation_provider is GenerationProvider.openai:
            if not self.external_llm_enabled:
                raise ValueError("generation_provider=openai requires external_llm_enabled=true")
            if "openai" not in self.external_provider_allowlist_set:
                raise ValueError(
                    "openai provider must be on external_provider_allowlist when selected"
                )
            if not self.openai_model:
                raise ValueError("openai_model is required when the OpenAI provider is selected")
        return self

    def read_openai_api_key(self) -> str | None:
        """Read the OpenAI key from its secret file. Never cached, never logged."""
        if self.openai_api_key_file is None:
            return None
        if not self.openai_api_key_file.exists():
            return None
        return self.openai_api_key_file.read_text(encoding="utf-8").strip() or None


@lru_cache
def get_settings() -> Settings:
    return Settings()

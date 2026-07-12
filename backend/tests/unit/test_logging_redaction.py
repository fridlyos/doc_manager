from __future__ import annotations

from doc_manager.core.logging import _redact_processor


def test_secrets_and_content_are_redacted() -> None:
    event = {
        "event": "provider_call",
        "api_key": "sk-secret",
        "database_url": "postgresql://u:p@h/db",
        "prompt": "confidential question",
        "provider": "openai",
        "duration_ms": 42,
    }
    out = _redact_processor(None, "info", dict(event))
    assert out["api_key"] == "***redacted***"
    assert out["database_url"] == "***redacted***"
    assert out["prompt"] == "***redacted***"
    # Non-sensitive operational fields survive.
    assert out["provider"] == "openai"
    assert out["duration_ms"] == 42

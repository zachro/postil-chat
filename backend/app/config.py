"""Load settings from config.yaml (path overridable via POSTIL_CONFIG)."""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ModelConfig:
    id: str
    label: str
    provider: str
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    # Tokens the server actually gives the model (for Ollama, its num_ctx /
    # OLLAMA_CONTEXT_LENGTH, not the model's maximum). Prompts past it are truncated.
    context_window: int | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Settings:
    database_url: str
    default_model: str
    models: dict[str, ModelConfig]
    summarizer_model: str | None = None
    token_budget: int = 8000  # fallback for models without a context_window
    send_with_enter: bool = False  # composer: Enter sends (Shift+Enter for a new line)

    def budget_for(self, model_id: str) -> int:
        return self.models[model_id].context_window or self.token_budget


def parse_settings(raw: dict[str, Any], root: Path = REPO_ROOT) -> Settings:
    models = {m["id"]: ModelConfig(**m) for m in raw.get("models", [])}
    default_model = raw["default_model"]
    if default_model not in models:
        raise ValueError(f"default_model {default_model!r} is not in models")
    summarizer_model = raw.get("summarizer_model")
    if summarizer_model is not None and summarizer_model not in models:
        raise ValueError(f"summarizer_model {summarizer_model!r} is not in models")
    db_path = root / raw.get("database_path", "data/postil.db")
    return Settings(
        database_url=f"sqlite:///{db_path}",
        default_model=default_model,
        models=models,
        summarizer_model=summarizer_model,
        token_budget=raw.get("token_budget", 8000),
        send_with_enter=raw.get("send_with_enter", False),
    )


@lru_cache
def get_settings() -> Settings:
    path = Path(os.environ.get("POSTIL_CONFIG", REPO_ROOT / "config.yaml"))
    with path.open() as f:
        return parse_settings(yaml.safe_load(f))

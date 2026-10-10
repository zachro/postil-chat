from app.config import ModelConfig
from app.llm.base import ChatMessage, LLMProvider
from app.llm.openai_compat import OpenAICompatProvider

__all__ = ["ChatMessage", "LLMProvider", "get_provider"]


def get_provider(config: ModelConfig) -> LLMProvider:
    if config.provider == "openai_compat":
        return OpenAICompatProvider(config)
    raise ValueError(f"Unknown provider {config.provider!r} for model {config.id!r}")

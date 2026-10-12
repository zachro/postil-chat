from app.config import ModelConfig
from app.llm.anthropic import AnthropicProvider
from app.llm.base import ChatMessage, LLMProvider, TokenCount, Usage, estimate_tokens
from app.llm.openai_compat import OpenAICompatProvider

__all__ = ["ChatMessage", "LLMProvider", "TokenCount", "Usage", "estimate_tokens", "get_provider"]


def get_provider(config: ModelConfig) -> LLMProvider:
    if config.provider == "openai_compat":
        return OpenAICompatProvider(config)
    if config.provider == "anthropic":
        return AnthropicProvider(config)
    raise ValueError(f"Unknown provider {config.provider!r} for model {config.id!r}")

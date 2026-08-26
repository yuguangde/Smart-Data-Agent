"""Application configuration via pydantic-settings (.env)."""
from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"
    QWEN = "qwen"


class CheckpointerKind(StrEnum):
    MEMORY = "memory"
    SQLITE = "sqlite"


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Strongly typed application settings sourced from the environment / .env."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -------- Server --------
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    log_level: str = Field(default="INFO")

    # -------- LLM --------
    llm_provider: LLMProvider = Field(default=LLMProvider.OPENAI)

    openai_api_key: str = Field(default="")
    openai_model: str = Field(default="gpt-4o-mini")
    openai_base_url: str = Field(default="")
    openai_temperature: float = Field(default=0.7)

    anthropic_api_key: str = Field(default="")
    anthropic_model: str = Field(default="claude-sonnet-4-5")

    deepseek_api_key: str = Field(default="")
    deepseek_base_url: str = Field(default="https://api.deepseek.com")
    deepseek_model: str = Field(default="deepseek-chat")

    qwen_api_key: str = Field(default="")
    qwen_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    qwen_model: str = Field(default="qwen-plus")

    max_tokens: int = Field(default=2048)
    max_iterations: int = Field(default=8)

    # -------- Memory --------
    checkpointer: CheckpointerKind = Field(default=CheckpointerKind.MEMORY)
    sqlite_path: str = Field(default=str(BASE_DIR / "data" / "chat.db"))

    # -------- Behavior --------
    hitl: bool = Field(
        default=False,
        description="Enable Human-in-the-Loop interrupt before agent node.",
    )

    # -------- LangSmith --------
    langsmith_tracing: bool = Field(default=False)
    langsmith_api_key: str = Field(default="")
    langsmith_project: str = Field(default="smart-data-agent")

    @property
    def sqlite_path_resolved(self) -> Path:
        path = Path(self.sqlite_path)
        if not path.is_absolute():
            path = BASE_DIR / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    settings = Settings()
    _apply_langsmith_env(settings)
    return settings


def _apply_langsmith_env(s: Settings) -> None:
    """Bridge pydantic settings into the env vars LangSmith SDK reads."""
    import os

    if s.langsmith_tracing:
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        if s.langsmith_api_key:
            os.environ["LANGSMITH_API_KEY"] = s.langsmith_api_key
        if s.langsmith_project:
            os.environ["LANGSMITH_PROJECT"] = s.langsmith_project

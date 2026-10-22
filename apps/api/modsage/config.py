from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MODSAGE_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )
    database_url: str = "sqlite:///./data/modsage.db"
    steam_api_key: str = Field(default="", repr=False)
    llm_api_key: str = Field(default="", repr=False)
    llm_base_url: str = Field(default="https://api.openai.com/v1", repr=False)
    llm_model: str = ""
    admin_token: str = Field(default="", repr=False)
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    llm_timeout_seconds: float = Field(default=15.0, ge=1, le=25)
    llm_max_output_tokens: int = Field(default=900, ge=64, le=8192)
    llm_token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    llm_temperature: float | None = Field(default=0, ge=0, le=2)
    provider_calls_enabled: bool = True
    provider_daily_call_limit: int = Field(default=200, ge=0, le=1000000)
    provider_minute_call_limit: int = Field(default=30, ge=0, le=10000)
    recommendation_timeout_seconds: float = Field(default=25.0, ge=1, le=60)
    recommendation_worker_enabled: bool = True
    recommendation_poll_seconds: float = Field(default=0.5, ge=0.05, le=10)
    recommendation_lease_seconds: int = Field(default=30, ge=5, le=120)
    recommendation_max_attempts: int = Field(default=3, ge=1, le=5)
    recommendation_queue_limit: int = Field(default=100, ge=1, le=1000)
    recommendation_sse_seconds: float = Field(default=25.0, ge=1, le=30)
    recommendation_stream_limit: int = Field(default=32, ge=1, le=200)
    request_limit_per_minute: int = Field(default=20, ge=1, le=1000)
    retrieval_mode: Literal["bm25", "hybrid"] = "bm25"
    retrieval_catalog_limit: int = Field(default=5000, ge=100, le=10000)
    retrieval_candidate_limit: int = Field(default=50, ge=10, le=200)
    embedding_api_key: str = Field(default="", repr=False)
    embedding_base_url: str = Field(default="", repr=False)
    embedding_model: str = Field(default="", max_length=200)
    embedding_response_model: str = Field(default="", max_length=200)
    embedding_dimensions: int | None = Field(default=None, ge=1, le=4096)
    embedding_timeout_seconds: float = Field(default=10.0, ge=1, le=20)
    embedding_min_similarity: float = Field(default=0.35, ge=0, le=1)
    refresh_enabled: bool = False
    refresh_source: Literal["api", "public"] = "api"
    refresh_hour: int = Field(default=8, ge=0, le=23)
    refresh_minute: int = Field(default=0, ge=0, le=59)
    refresh_limit_per_sort: int = Field(default=20, ge=1, le=100)
    refresh_enrich_limit: int = Field(default=0, ge=0, le=20)
    refresh_max_attempts: int = Field(default=3, ge=1, le=5)
    refresh_lease_seconds: int = Field(default=60, ge=10, le=600)
    refresh_request_budget_seconds: float = Field(default=180, ge=1, le=900)
    refresh_poll_seconds: float = Field(default=15, ge=1, le=60)

    @field_validator("embedding_dimensions", "llm_temperature", mode="before")
    @classmethod
    def empty_dimension_is_unspecified(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator(
        "steam_api_key",
        "admin_token",
        "llm_api_key",
        "llm_base_url",
        "llm_model",
        "embedding_api_key",
        "embedding_base_url",
        "embedding_model",
        "embedding_response_model",
    )
    @classmethod
    def trim_provider_configuration(cls, value):
        return value.strip()

    @property
    def llm_configured(self) -> bool:
        from modsage.provider_config import provider_configuration

        return provider_configuration(self, "llm")["configured"]

    @property
    def embedding_configured(self) -> bool:
        from modsage.provider_config import provider_configuration

        return provider_configuration(self, "embedding")["configured"]


@lru_cache
def get_settings() -> Settings:
    return Settings()

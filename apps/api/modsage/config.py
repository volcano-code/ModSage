from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MODSAGE_", env_file=".env", extra="ignore", case_sensitive=False
    )
    database_url: str = "sqlite:///./data/modsage.db"
    steam_api_key: str = ""
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    admin_token: str = ""
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    llm_timeout_seconds: float = Field(default=15.0, ge=1, le=25)
    recommendation_timeout_seconds: float = Field(default=25.0, ge=1, le=60)
    request_limit_per_minute: int = Field(default=20, ge=1, le=1000)
    retrieval_mode: Literal["bm25", "hybrid"] = "bm25"
    retrieval_catalog_limit: int = Field(default=5000, ge=100, le=10000)
    retrieval_candidate_limit: int = Field(default=50, ge=10, le=200)
    embedding_api_key: str = ""
    embedding_base_url: str = Field(default="", max_length=2000)
    embedding_model: str = Field(default="", max_length=200)
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

    @field_validator("embedding_dimensions", mode="before")
    @classmethod
    def empty_dimension_is_unspecified(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("embedding_api_key", "embedding_base_url", "embedding_model")
    @classmethod
    def trim_embedding_configuration(cls, value):
        return value.strip()

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key.strip() and self.llm_model.strip())

    @property
    def embedding_configured(self) -> bool:
        return bool(
            self.embedding_api_key.strip()
            and self.embedding_base_url.strip()
            and self.embedding_model.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

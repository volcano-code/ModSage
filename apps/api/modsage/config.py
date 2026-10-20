from functools import lru_cache

from pydantic import Field
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

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key.strip() and self.llm_model.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()

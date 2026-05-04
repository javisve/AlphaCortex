from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # API Security
    api_key: str = "change_this"

    # Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash-lite"

    # Database
    database_url: str = "postgresql://fundmanager:password@localhost:5432/fundmanager"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Screener
    screener_top_n: int = 30
    max_positions: int = 10

    # Debug
    debug: bool = False

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()

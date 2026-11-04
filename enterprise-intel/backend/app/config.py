from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str = "postgresql+psycopg://app:app@localhost:5432/enterprise_intel"
    jwt_secret: str = DEFAULT_JWT_SECRET
    access_token_minutes: int = 30
    refresh_token_days: int = 7
    cors_origins: str = "http://localhost:3000"
    rate_limit_enabled: bool = True

    upload_dir: str = "/data/uploads"
    max_upload_mb: int = 20
    chunk_target_tokens: int = 500
    chunk_overlap_tokens: int = 60

    embedding_provider: str = "hash"  # "hash" (offline, lexical) or "openai"
    openai_api_key: str = ""
    # Relevance gate thresholds; tune on the eval set.
    min_similarity: float = 0.2
    min_keyword_rank: float = 0.05

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production and (s.jwt_secret == DEFAULT_JWT_SECRET or len(s.jwt_secret) < 32):
        raise RuntimeError("JWT_SECRET must be a long random value in production")
    return s


settings = get_settings()

from functools import lru_cache

from pydantic import field_validator
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

    embedding_provider: str = "local"  # "local" (MiniLM, free), "openai" or "hash" (offline, lexical)
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_fast_model: str = "claude-haiku-5-5"
    anthropic_smart_model: str = "claude-sonnet-5-5"
    gemini_model: str = "gemini-2.5-flash"
    llm_timeout_s: float = 30
    llm_max_retries: int = 2
    circuit_threshold: int = 3
    circuit_cooldown_s: float = 60

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    groundedness_threshold: float = 0.6
    max_agent_steps: int = 6
    agent_timeout_s: float = 90

    rerank_enabled: bool = False  # local cross-encoder re-ordering of the top candidates
    # Relevance gate thresholds; tune on the eval set.
    min_similarity: float = 0.4  # tuned for the local MiniLM embedder on the eval set (see eval/report.md)
    min_keyword_rank: float = 0.05

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, url: str) -> str:
        """Hosted providers (Supabase, Neon, Render) hand out postgres:// or postgresql:// URLs."""
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url

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

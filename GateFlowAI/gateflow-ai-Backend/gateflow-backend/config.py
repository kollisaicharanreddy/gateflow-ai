"""
config.py — App settings loaded from .env
"""
from functools import lru_cache
from typing import List
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    APP_ENV: str = "development"
    APP_NAME: str = "GateFlow AI"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Google OAuth2
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/auth/google/callback"

    # Database
    DATABASE_URL: str

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # Background jobs — how often to scan for overstayed visitors (lower = faster alerts, more DB load)
    OVERSTAY_POLL_INTERVAL_MINUTES: int = 1

    # CORS
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:5173,http://localhost:5174,http://127.0.0.1:5173,http://127.0.0.1:5174"

    # Public SPA base (no trailing slash). Invite links: {PUBLIC_APP_URL}/invite/{jwt}
    # Default matches Vite's default dev port (5173). Override PUBLIC_APP_URL in .env for staging/prod.
    PUBLIC_APP_URL: str = "http://localhost:5173"

    # File Upload
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_SIZE_MB: int = 20

    # AI APIs (future)
    OPENAI_API_KEY: str = ""
    GEMINI_API_KEY: str = ""

    # RAG microservice base URL (no trailing slash)
    RAG_BASE_URL: str = "http://localhost:8001"

    # AWS S3 — file storage (replaces local uploads/ folder)
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "ap-south-2"
    S3_BUCKET_NAME: str = "gateflow-uploads"

    # Error Tracking (future)
    SENTRY_DSN: str = ""

    @property
    def allowed_origins_list(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",")]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"

    @property
    def is_development(self) -> bool:
        return self.APP_ENV.lower() == "development"

    @field_validator("DATABASE_URL")
    @classmethod
    def must_use_asyncpg(cls, v: str) -> str:
        if v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use postgresql+asyncpg://")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()

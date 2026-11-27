from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = "auth-service"
    database_url: str = "sqlite:///./data/auth_service.db"

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://127.0.0.1:8000/api/v1/auth/google/callback"
    google_user_token_path: str = "./secrets/google_user_token.json"

    jwt_secret_key: str = "dev-insecure-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7

    kafka_enabled: bool = False
    kafka_bootstrap_servers: str = "kafka:9092"


@lru_cache
def get_auth_settings() -> AuthServiceSettings:
    return AuthServiceSettings()

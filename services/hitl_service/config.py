from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class HITLServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = "hitl-service"
    database_url: str = "sqlite:///./data/hitl_service.db"

    use_real_mcp: bool = False
    github_personal_access_token: str = ""

    kafka_enabled: bool = False
    kafka_bootstrap_servers: str = "kafka:9092"


@lru_cache
def get_hitl_settings() -> HITLServiceSettings:
    return HITLServiceSettings()

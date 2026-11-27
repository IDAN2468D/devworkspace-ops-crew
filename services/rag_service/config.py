from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class RAGServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = "rag-service"
    chroma_persist_dir: str = "./data/chroma"
    chroma_collection_name: str = "drive_project_specs"

    kafka_enabled: bool = False
    kafka_bootstrap_servers: str = "kafka:9092"


@lru_cache
def get_rag_settings() -> RAGServiceSettings:
    return RAGServiceSettings()

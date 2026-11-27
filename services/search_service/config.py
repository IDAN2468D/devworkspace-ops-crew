from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class SearchServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = "search-service"
    elasticsearch_enabled: bool = False
    elasticsearch_url: str = "http://elasticsearch:9200"
    specs_index: str = "ops-specs"
    events_index: str = "ops-events"

    kafka_enabled: bool = False
    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_group_id: str = "search-service-indexer"


@lru_cache
def get_search_settings() -> SearchServiceSettings:
    return SearchServiceSettings()

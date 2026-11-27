from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class OpsAgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = "ops-agent-service"
    google_api_key: str = ""
    gemini_model_id: str = "gemini-2.5-flash"

    # Inter-service REST URLs
    hitl_service_url: str = "http://hitl-service:8002"
    rag_service_url: str = "http://rag-service:8003"
    search_service_url: str = "http://search-service:8005"

    # MCP Configuration
    use_real_mcp: bool = False
    google_oauth_credentials_path: str = "./secrets/gdrive-oauth.json"
    google_user_token_path: str = "./secrets/google_user_token.json"
    mcp_gmail_cmd: str = "npx -y @gongrzhe/server-gmail-autoauth-mcp"
    mcp_drive_cmd: str = "npx -y @modelcontextprotocol/server-gdrive"
    mcp_github_cmd: str = "npx -y @modelcontextprotocol/server-github"
    github_personal_access_token: str = ""
    github_default_repo: str = "IDAN2468D/mcp-ai-ide-core"

    # Kafka
    kafka_enabled: bool = False
    kafka_bootstrap_servers: str = "kafka:9092"


@lru_cache
def get_ops_settings() -> OpsAgentSettings:
    return OpsAgentSettings()

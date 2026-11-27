from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    google_api_key: str = ""
    gemini_model_id: str = "gemini-2.5-flash"

    # Database (PostgreSQL in Docker Compose, SQLite fallback locally)
    database_url: str = "sqlite:///./data/hitl.db"

    # ChromaDB RAG
    chroma_persist_dir: str = "./data/chroma"
    chroma_collection_name: str = "drive_project_specs"

    # Google OAuth 2.0 (Login / Registration + Google MCP tokens)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://127.0.0.1:8000/api/v1/auth/google/callback"
    google_oauth_credentials_path: str = "./secrets/gdrive-oauth.json"
    google_user_token_path: str = "./secrets/google_user_token.json"

    # JWT Session Auth
    jwt_secret_key: str = "dev-insecure-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 days
    auth_required: bool = False  # Set true in production to require Bearer JWT on all /ops & /hitl routes

    # MCP Configuration (set USE_REAL_MCP=true after connecting Google OAuth & GitHub PAT)
    use_real_mcp: bool = False
    mcp_gmail_cmd: str = "npx -y @gongrzhe/server-gmail-autoauth-mcp"
    mcp_drive_cmd: str = "npx -y @modelcontextprotocol/server-gdrive"
    mcp_github_cmd: str = "npx -y @modelcontextprotocol/server-github"

    # GitHub Credentials
    github_personal_access_token: str = ""
    github_default_repo: str = "IDAN2468D/mcp-ai-ide-core"


@lru_cache
def get_settings() -> Settings:
    return Settings()

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Global Helios settings.

    All settings can be overridden with environment variables prefixed by HELIOS_.
    Example:
      HELIOS_DATABASE_URL=postgresql+psycopg2://...

    The gateway is designed to run with ZERO configuration using the built-in
    mock provider. Free-tier LLM providers (Groq, OpenRouter, Gemini) only need
    an API key pasted into the matching HELIOS_*_API_KEY variable.
    """

    app_name: str = "Helios Gateway"
    environment: str = "dev"

    # Database
    database_url: str = "postgresql+psycopg2://helios:helios@localhost:5432/helios"

    # Default provider/model behavior
    # Options: mock | openai | groq | openrouter | gemini | anthropic
    default_provider: str = "mock"
    default_model: str = "mock-model-1"

    # Router v2: comma-separated ordered fallback providers, e.g. "groq,mock"
    fallback_providers: str = ""
    # Provider to prefer for high/critical-risk requests (empty = default)
    high_risk_provider: str = ""

    # --- Free providers ------------------------------------------------------
    # Groq (OpenAI-compatible, free tier): https://console.groq.com/keys
    groq_api_key: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    default_groq_model: str = "llama-3.3-70b-versatile"

    # OpenRouter (OpenAI-compatible, free models): https://openrouter.ai/keys
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    default_openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free"

    # Google Gemini (free tier): https://aistudio.google.com/apikey
    gemini_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    default_gemini_model: str = "gemini-1.5-flash"

    # --- Paid providers (optional) ------------------------------------------
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    default_openai_model: str = "gpt-4o-mini"

    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    anthropic_version: str = "2023-06-01"
    default_anthropic_model: str = "claude-3-5-sonnet-latest"

    # --- Embeddings (Phase 2: knowledge retrieval) ---------------------------
    # Options: mock | gemini | openai
    embedding_provider: str = "mock"
    # Vector column dimension. 1536 matches OpenAI text-embedding-3-small; use
    # 768 for Gemini text-embedding-004. Changing this after tables exist
    # requires a migration.
    embedding_dim: int = 1536
    default_gemini_embedding_model: str = "text-embedding-004"
    default_openai_embedding_model: str = "text-embedding-3-small"

    # Retrieval defaults
    retrieval_top_k: int = 3
    chunk_size: int = 500
    chunk_overlap: int = 50

    # Generation defaults
    default_max_tokens: int = 1024

    # --- Agent runtime + Tool Broker (V1 core) -------------------------------
    # Root directory tools may touch. Filesystem/shell/git tools are jailed
    # under this path; anything outside is denied by the permission layer.
    workspace_root: str = "./workspace"
    # Subprocess hard timeout for shell/git tools (seconds).
    shell_timeout_s: float = 30.0
    # Max bytes of tool output kept (rest truncated, noted in the trace).
    tool_output_max_bytes: int = 65536
    # Branches treated as production-protected for risk scoring.
    protected_branches: str = "main,master,production,release"
    # GitHub token used by the GitHub tool executors (never logged).
    github_token: str | None = None
    github_api_base: str = "https://api.github.com"
    # Max agent loop steps per run (runaway guard).
    agent_max_steps: int = 24

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="HELIOS_",
        extra="ignore",
    )


settings = Settings()

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://oriexa:oriexa@localhost:5432/oriexa"
    NEXTAUTH_SECRET: str = "dev-secret"
    ENCRYPTION_KEY: str = ""  # 64 hex chars = 32 bytes for AES-256-GCM
    CORS_ORIGINS: str = "http://localhost:3000"
    ENVIRONMENT: str = "development"

    # Orchestrator settings
    ORIEXA_API_BASE_URL: str = "http://localhost:3000/api/v1"
    ORIEXA_API_KEY: str = ""  # th_agent_ + 64 hex chars

    # LLM providers
    OPENROUTER_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""  # For direct Anthropic calls (opus)
    MOONSHOT_API_KEY: str = ""  # For Kimi models

    # Model IDs per tier — supports OpenRouter free, Anthropic direct, Moonshot
    FAST_MODEL: str = "minimax/minimax-m2.5"
    DEFAULT_MODEL: str = "minimax/minimax-m2.5"
    STRONG_MODEL: str = "anthropic/claude-sonnet-4.6"
    THINKING_MODEL: str = "moonshot/kimi-k2.5-thinking"  # Deep reasoning tasks

    # Frontend coding agent model tiers
    # CODING_MODEL        — primary execution model (prioritized for all frontend tasks)
    # CODING_STRONG_MODEL — escalated to when complexity == "high" or budget > 500
    # CODING_PLANNING_MODEL — used for the planning stage (strong reasoning required)
    # Fallback chain for CODING tier: glm-5 → minimax-m2.5 → gemini-3-flash → gpt-5.3-codex
    CODING_MODEL: str = "openrouter/z-ai/glm-5"
    CODING_STRONG_MODEL: str = "openrouter/openai/gpt-5.3-codex"
    CODING_PLANNING_MODEL: str = "openrouter/anthropic/claude-opus-4-6"
    CODING_ALT_MODEL_1: str = "openrouter/minimax/minimax-m2.5"
    CODING_ALT_MODEL_2: str = "openrouter/google/gemini-3-flash-preview"

    MAX_CONCURRENT_TASKS: int = 5
    TASK_POLL_INTERVAL: int = 30  # seconds
    SANDBOX_TIMEOUT: int = 120  # seconds
    WORKSPACE_ROOT: str = "/tmp/oriexa-workspaces"
    AGENT_WORKSPACE_DIR: str = "/tmp/oriexa-agent-works"
    ALLOWED_COMMANDS: str = "python,node,npm,npx,pip,git,gh,ls,cat,head,tail,grep,find,mkdir,cp,mv,rm,touch,echo,curl,wget,tsc,eslint,flake8,pytest,make,sh,bash,cd,pwd,which,env,sort,uniq,wc,tr,cut,sed,awk,diff,patch,tar,gzip,unzip,ssh-keygen,openssl,jq,xargs"
    BLOCKED_PATTERNS: str = "sudo,su ,chmod 777,rm -rf /,> /etc,> /dev"

    # Deployment pipeline settings
    GITHUB_TOKEN: str = ""  # For gh CLI authentication
    GITHUB_ORG: str = ""  # GitHub org/user for repo creation
    GITHUB_REPO_PREFIX: str = "oriexa-delivery"  # Prefix for generated repos
    VERCEL_DEPLOY_ENDPOINT: str = ""  # Legacy: URL of custom deploy endpoint
    VERCEL_TOKEN: str = ""  # Vercel CLI token (preferred over VERCEL_DEPLOY_ENDPOINT)
    VERCEL_ORG_ID: str = ""  # Vercel team/org ID
    VERCEL_PROJECT_ID: str = ""  # Vercel project ID
    VERCEL_USE_LINKED_PROJECT: bool = False  # Link to fixed project only when explicitly enabled

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()

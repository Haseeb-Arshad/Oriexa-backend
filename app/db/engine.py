from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


def _build_engine_params(raw_url: str) -> tuple[str, dict]:
    """
    Sanitize DATABASE_URL and return (clean_url, connect_args).

    Handles:
    - postgresql:// → postgresql+asyncpg://
    - Strips query params that must go through connect_args (prepared_statement_cache_size)
    - Auto-enables SSL for hosted Postgres (Supabase, Neon, Render, Railway)
    - Auto-disables prepared-statement cache for Supabase pooler (port 6543)
    """
    url = raw_url.strip()

    # Normalize driver prefix — only add +asyncpg if bare postgresql://
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]

    # Parse the URL so we can inspect / clean the query string
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    connect_args: dict = {"command_timeout": 60}

    # Pull prepared_statement_cache_size out of the URL (asyncpg wants it
    # in connect_args as statement_cache_size, and some SQLAlchemy versions
    # reject it in the URL string causing ArgumentError).
    if "prepared_statement_cache_size" in params:
        val = params.pop("prepared_statement_cache_size")[0]
        connect_args["statement_cache_size"] = int(val)

    # Reconstruct URL without the stripped params
    new_query = urlencode({k: v[0] for k, v in params.items()})
    clean_url = urlunparse(parsed._replace(query=new_query))

    # Auto-SSL for known hosted-Postgres providers
    hosted = ("supabase.co", "supabase.com", "neon.tech", "render.com", "railway.app")
    if any(h in clean_url for h in hosted) and "ssl" not in clean_url:
        connect_args["ssl"] = "require"

    # Supabase Supavisor transaction pooler (port 6543) forbids prepared statements
    if ":6543/" in clean_url or "pooler.supabase.com" in clean_url:
        connect_args.setdefault("statement_cache_size", 0)

    return clean_url, connect_args


_db_url, _connect_args = _build_engine_params(settings.DATABASE_URL)

engine = create_async_engine(
    _db_url,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_recycle=300,
    pool_pre_ping=True,
    pool_timeout=30,
    connect_args=_connect_args,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with async_session() as session:
        yield session

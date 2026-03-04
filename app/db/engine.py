import re

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


def _build_engine_params(raw_url: str) -> tuple[str, dict]:
    """
    Normalize DATABASE_URL and build connect_args without urlparse
    (urlparse breaks if the password contains special characters like @ # + etc).
    """
    url = raw_url.strip()

    # postgresql:// → postgresql+asyncpg://  (no-op if already has +asyncpg)
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]

    connect_args: dict = {"command_timeout": 60}

    # Pull prepared_statement_cache_size out of the query string — some
    # SQLAlchemy versions reject it in the URL and raise ArgumentError.
    m = re.search(r"[?&]prepared_statement_cache_size=(\d+)", url)
    if m:
        connect_args["statement_cache_size"] = int(m.group(1))
        url = re.sub(r"[?&]prepared_statement_cache_size=\d+", "", url)
        url = url.rstrip("?&")

    # Auto-enable SSL for known hosted-Postgres providers
    hosted = ("supabase.co", "supabase.com", "neon.tech", "render.com", "railway.app")
    if any(h in url for h in hosted) and "ssl" not in url:
        connect_args["ssl"] = "require"

    # Supabase Supavisor transaction pooler (port 6543) forbids prepared statements
    if ":6543/" in url or "pooler.supabase.com" in url:
        connect_args.setdefault("statement_cache_size", 0)

    return url, connect_args


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

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

_db_url = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
_connect_args: dict = {"command_timeout": 60}

# Supabase (and most managed Postgres) requires SSL on the wire.
# asyncpg accepts ssl=True (verify cert) or ssl='require' (skip verify).
# We check for the common hosted-postgres hostnames but also honour an
# explicit ?sslmode= or ?ssl= query parameter already in the URL.
_needs_ssl = any(
    kw in _db_url
    for kw in ("supabase.co", "supabase.com", "neon.tech", "render.com", "railway.app")
)
if _needs_ssl and "ssl" not in _db_url:
    _connect_args["ssl"] = "require"

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

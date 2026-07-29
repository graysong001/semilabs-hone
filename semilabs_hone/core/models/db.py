"""SQLAlchemy engine, session factory, and database initialization.

Shared SQLite engine at data/factory.db. Engine is created lazily so
that test fixtures can monkeypatch config.DB_URL before the first
engine is instantiated.

PRD §6.4 DB operations rule:
- ``connect_args={"check_same_thread": False}`` — Web + Worker processes
  share one SQLite file; cross-thread access must not raise.
- ``timeout=15`` — mitigate ``database is locked`` under dual-process load.
- ``PRAGMA journal_mode=WAL`` — concurrent readers + single writer (per
  connection, set via a connect event listener).
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

_engine = None
_Session = None


def _sqlite_connect_args() -> dict:
    """PRD §6.4: cross-thread + 15s lock timeout for the shared SQLite."""
    return {"check_same_thread": False, "timeout": 15}


def _attach_sqlite_pragmas(engine) -> None:
    """Set PRAGMA journal_mode=WAL on every raw SQLite connection.

    WAL permits concurrent readers alongside the single writer, matching
    the dual-process (web + worker) access pattern. Set per-connection via
    a ``connect`` event so pooled/reopened connections stay WAL.
    """
    @event.listens_for(engine, "connect")
    def _set_wal(dbapi_conn, _connection_record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")  # standard WAL pairing
        cur.close()


def get_engine():
    """Return the shared engine, creating it lazily from config.DB_URL."""
    global _engine, _Session
    if _engine is None:
        from config import DB_URL
        _engine = create_engine(
            DB_URL,
            echo=False,
            connect_args=_sqlite_connect_args(),
        )
        _attach_sqlite_pragmas(_engine)
        _Session = sessionmaker(bind=_engine)
    return _engine


def _get_session_maker():
    """Return the session factory, ensuring engine exists."""
    get_engine()
    return _Session


def set_engine_for_test(engine_or_url):
    """Override the engine for testing (accepts an Engine instance or a URL string)."""
    global _engine, _Session
    if isinstance(engine_or_url, str):
        _engine = create_engine(
            engine_or_url,
            echo=False,
            connect_args=_sqlite_connect_args(),
        )
        _attach_sqlite_pragmas(_engine)
    else:
        _engine = engine_or_url
    _Session = sessionmaker(bind=_engine)


def reset_engine():
    """Reset the lazy engine (for test cleanup)."""
    global _engine, _Session
    _engine = None
    _Session = None


Base = declarative_base()


def init_db() -> None:
    """Create all tables if they do not exist."""
    from semilabs_hone.core.models import account, keyword, task, post, comment  # noqa: F401
    Base.metadata.create_all(get_engine())


def get_session():
    """Return a new database session."""
    return _get_session_maker()()

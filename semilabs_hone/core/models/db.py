"""SQLAlchemy engine, session factory, and database initialization.

Shared SQLite engine at data/factory.db. Engine is created lazily so
that test fixtures can monkeypatch config.DB_URL before the first
engine is instantiated.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

_engine = None
_Session = None


def get_engine():
    """Return the shared engine, creating it lazily from config.DB_URL."""
    global _engine, _Session
    if _engine is None:
        from config import DB_URL
        _engine = create_engine(DB_URL, echo=False)
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
        _engine = create_engine(engine_or_url, echo=False)
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

"""SQLAlchemy engine, session factory, and database initialization.

Shared SQLite engine at data/factory.db. All modules import from here
for a single connection point to the shared database.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from config import DB_URL

Engine = create_engine(DB_URL, echo=False)

Session = sessionmaker(bind=Engine)

Base = declarative_base()


def init_db() -> None:
    """Create all tables if they do not exist."""
    # Import all ORM models so Base.metadata knows about them
    from semilabs_hone.core.models import account, keyword, task, post, comment  # noqa: F401
    Base.metadata.create_all(Engine)


def get_session():
    """Return a new database session."""
    return Session()

"""SQLite engine plumbing shared by every storefront server (mirrors amazon's)."""

from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

_db_path: str = "./storefront.db"
_engine = None


def set_db_path(path: str) -> None:
    global _db_path, _engine
    _db_path = path
    _engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(f"sqlite:///{_db_path}",
                                connect_args={"check_same_thread": False}, echo=False)
    return _engine


def init_db() -> None:
    import caveat.envs._storefront.models  # noqa: F401  (register tables)
    SQLModel.metadata.create_all(get_engine())


def get_session():
    with Session(get_engine()) as session:
        yield session

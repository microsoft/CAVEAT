from sqlmodel import SQLModel, Session, create_engine

_db_path: str = "./caveat_shop.db"
_engine = None


def set_db_path(path: str):
    """Set the database file path. Must be called before init_db()."""
    global _db_path, _engine
    _db_path = path
    _engine = None


def get_engine():
    """Get or create the database engine."""
    global _engine
    if _engine is None:
        database_url = f"sqlite:///{_db_path}"
        _engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            echo=False
        )
    return _engine


def init_db():
    """Initialize database and create all tables."""
    # Import models to ensure they're registered with SQLModel metadata
    import backend.models  # noqa: F401
    SQLModel.metadata.create_all(get_engine())


def get_session():
    """Get a database session."""
    with Session(get_engine()) as session:
        yield session


# Proxy for backwards compatibility
class _EngineProxy:
    """Proxy to lazily get the engine."""
    def __getattr__(self, name):
        return getattr(get_engine(), name)


engine = _EngineProxy()

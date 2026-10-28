from typing import Generator
from sqlmodel import Session, SQLModel, create_engine
from app.config import settings

connect_args = {}
if settings.sqlalchemy_database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.sqlalchemy_database_url,
    echo=False,
    pool_pre_ping=True,
    connect_args=connect_args,
)


def init_db() -> None:
    """Create all SQLModel tables if they don't exist yet."""
    import app.models  # noqa: F401
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session

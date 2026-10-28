from typing import Generator
from sqlalchemy import inspect, text
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
    """Create all SQLModel tables and apply lightweight column migrations."""
    import app.models  # noqa: F401
    SQLModel.metadata.create_all(engine)

    # Ensure `county` column exists on existing PostgreSQL / SQLite tables
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table_name in ("foreclosure_listings", "zip_trends"):
            if inspector.has_table(table_name):
                cols = {c["name"] for c in inspector.get_columns(table_name)}
                if "county" not in cols:
                    conn.execute(
                        text(
                            f"ALTER TABLE {table_name} ADD COLUMN county VARCHAR DEFAULT 'Duval'"
                        )
                    )


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session

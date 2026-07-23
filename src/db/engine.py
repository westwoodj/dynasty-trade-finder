"""SQLModel engine creation and schema initialization."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import SQLModel, create_engine

# Import models so they register on SQLModel.metadata before create_all.
from . import models  # noqa: F401

DEFAULT_DB_PATH = Path("data") / "dtf.db"


def get_engine(db_path: Path | str = DEFAULT_DB_PATH, echo: bool = False) -> Engine:
    """Create a SQLite engine for *db_path* and ensure the schema exists.

    ``check_same_thread=False`` lets Streamlit's rerun threads share one
    engine (the singleton is guarded by ``st.cache_resource``).
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path}",
        echo=echo,
        connect_args={"check_same_thread": False},
    )
    init_db(engine)
    return engine


def default_engine() -> Engine:
    return get_engine()


def init_db(engine: Engine) -> None:
    """Create any missing tables."""
    SQLModel.metadata.create_all(engine)

"""SQLModel engine creation and schema initialization.

The store runs on **Cloud SQL for PostgreSQL** when deployed and falls back to
a local **SQLite** file for development. Which one is used is decided purely
from the environment, so no code path is deployment-specific:

* ``DATABASE_URL`` — a full SQLAlchemy URL, used verbatim (with a bare
  ``postgresql://`` normalized to the pure-Python ``pg8000`` driver). Handy for
  any managed Postgres.
* ``INSTANCE_CONNECTION_NAME`` + ``DB_USER`` / ``DB_PASS`` / ``DB_NAME`` — the
  Cloud Run → Cloud SQL pattern: connect over the Unix socket that Cloud Run
  mounts at ``/cloudsql/<INSTANCE_CONNECTION_NAME>`` when the service is
  deployed with ``--add-cloudsql-instances``.
* neither set — a SQLite file at ``data/dtf.db`` (local dev / tests).
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.engine import URL, make_url
from sqlmodel import SQLModel, create_engine

# Import models so they register on SQLModel.metadata before create_all.
from . import models  # noqa: F401

DEFAULT_DB_PATH = Path("data") / "dtf.db"

# Cloud Run mounts the Cloud SQL Unix socket under this directory.
_CLOUD_SQL_SOCKET_DIR = "/cloudsql"


def _database_url() -> URL | str | None:
    """Resolve a Postgres SQLAlchemy URL from the environment, or ``None``.

    ``None`` means "no managed database configured" — the caller falls back to
    SQLite. This never raises on a partial config; it simply returns ``None``
    so a half-set environment can't silently point production at SQLite while
    looking configured — callers that require Postgres should assert on it.
    """
    raw = os.environ.get("DATABASE_URL")
    if raw:
        url = make_url(raw)
        # Default a bare postgresql:// to pg8000 (pure-Python, no build deps —
        # matches what ships in the slim image).
        if url.drivername == "postgresql":
            url = url.set(drivername="postgresql+pg8000")
        return url

    instance = os.environ.get("INSTANCE_CONNECTION_NAME")
    db_user = os.environ.get("DB_USER")
    db_pass = os.environ.get("DB_PASS")
    db_name = os.environ.get("DB_NAME")
    if instance and db_user and db_pass and db_name:
        # pg8000 connects to the Cloud SQL Unix socket via the ``unix_sock``
        # query arg pointing at the .s.PGSQL.5432 socket file.
        socket_path = f"{_CLOUD_SQL_SOCKET_DIR}/{instance}/.s.PGSQL.5432"
        return URL.create(
            drivername="postgresql+pg8000",
            username=db_user,
            password=db_pass,
            database=db_name,
            query={"unix_sock": socket_path},
        )
    return None


def get_engine(db_path: Path | str = DEFAULT_DB_PATH, echo: bool = False) -> Engine:
    """Create the store engine and ensure the schema exists.

    Uses Cloud SQL Postgres when the environment configures it (see module
    docstring); otherwise a SQLite file at *db_path*. *db_path* only applies to
    the SQLite fallback.
    """
    url = _database_url()
    if url is not None:
        engine = create_engine(
            url,
            echo=echo,
            # Verify connections before use (Cloud SQL drops idle ones) and
            # recycle before Cloud SQL's ~real idle timeout. Keep the pool small
            # since Cloud Run fans out across instances, not threads.
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=5,
            max_overflow=2,
        )
    else:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            f"sqlite:///{path}",
            echo=echo,
            # check_same_thread=False lets Streamlit's rerun threads share one
            # engine (the singleton is guarded by st.cache_resource).
            connect_args={"check_same_thread": False},
        )
    init_db(engine)
    return engine


def default_engine() -> Engine:
    return get_engine()


def init_db(engine: Engine) -> None:
    """Create any missing tables."""
    SQLModel.metadata.create_all(engine)

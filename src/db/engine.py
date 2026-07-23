"""SQLModel engine creation and schema initialization.

The store runs on a **managed Postgres** (Neon serverless Postgres in
production) when deployed and falls back to a local **SQLite** file for
development. Which one is used is decided purely from the environment, so no
code path is deployment-specific:

* ``DATABASE_URL`` — a full SQLAlchemy URL, used verbatim (with a bare
  ``postgresql://`` / ``postgres://`` normalized to the pure-Python ``pg8000``
  driver). This is the production path: point it at Neon (or any managed
  Postgres). Neon requires TLS and ships URLs with ``?sslmode=require``; since
  ``pg8000`` doesn't understand libpq's ``sslmode`` param, that is translated
  into an ``ssl_context`` connect arg (see :func:`get_engine`).
* ``INSTANCE_CONNECTION_NAME`` + ``DB_USER`` / ``DB_PASS`` / ``DB_NAME`` — the
  Cloud Run → Cloud SQL over-the-Unix-socket pattern, still supported for a
  Cloud SQL deployment but no longer the default managed target.
* neither set — a SQLite file at ``data/dtf.db`` (local dev / tests).
"""

from __future__ import annotations

import os
import ssl
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
        # Default a bare postgresql:// (or postgres://, as Neon sometimes emits)
        # to pg8000 (pure-Python, no build deps — matches the slim image).
        if url.drivername in ("postgresql", "postgres"):
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

    Uses a managed Postgres (Neon, or Cloud SQL) when the environment configures
    it (see module docstring); otherwise a SQLite file at *db_path*. *db_path*
    only applies to the SQLite fallback.
    """
    url = _database_url()
    if url is not None:
        connect_args: dict = {}
        if isinstance(url, URL):
            # pg8000 doesn't understand libpq's ``sslmode`` / ``channel_binding``
            # query params (those are psycopg2/libpq concepts). A managed
            # Postgres such as Neon requires TLS and ships URLs with
            # ``?sslmode=require``: translate an SSL-requiring mode into an
            # ``ssl_context`` connect arg, then strip the libpq-only params so
            # ``pg8000.connect()`` doesn't reject them. The Cloud SQL Unix-socket
            # URL carries neither param, so it stays plaintext over the socket.
            if url.query.get("sslmode") not in (None, "disable", "allow"):
                connect_args["ssl_context"] = ssl.create_default_context()
            url = url.difference_update_query(["sslmode", "channel_binding"])
        engine = create_engine(
            url,
            echo=echo,
            connect_args=connect_args,
            # Verify connections before use (managed Postgres drops idle ones —
            # Neon autosuspends) and recycle before that idle timeout. Keep the
            # pool small since Cloud Run fans out across instances, not threads.
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

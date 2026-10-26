from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from modsage.schema import SchemaMigrationError  # noqa: F401


class Base(DeclarativeBase):
    pass


def make_database(url: str):
    parsed = make_url(url)
    kwargs = {"pool_pre_ping": True}
    if parsed.drivername.startswith("sqlite"):
        if parsed.database and parsed.database != ":memory:":
            # SQLAlchemy treats '~' in SQLite URLs literally, rather than as HOME.
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 20}
        if parsed.database in (None, ":memory:"):
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    if parsed.drivername.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def sqlite_settings(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=20000")

    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def init_database(engine):
    from modsage.schema import upgrade_database

    return upgrade_database(engine)


def schema_status(engine):
    from modsage.schema import schema_status as inspect_schema

    return inspect_schema(engine)


def upgrade_database(engine):
    from modsage.schema import upgrade_database as upgrade_schema

    return upgrade_schema(engine)

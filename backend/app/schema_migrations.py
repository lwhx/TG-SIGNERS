"""P8 explicitly versioned additive Dashboard SQLite schema migrations.

SQLAlchemy creates missing tables; legacy column migrations remain additive.
Never silently downgrade a newer database. No credentials are inspected.
"""

from __future__ import annotations

from sqlalchemy import text

from .db import Base, engine, migrate_task_groups, now

CURRENT_SCHEMA = 3
MIGRATIONS = {
    1: "P6 task management groups and immutable revisions",
    2: "P7 persistent historical relay backfill batches",
    3: "P8 schema version tracking and release upgrades",
}
REQUIRED_TABLES = {
    1: {"accounts", "tasks", "runs", "settings", "task_versions"},
    2: {"relay_backfills"},
    3: {"schema_migrations"},
}


def migrate_schema() -> dict:
    """Idempotent startup migration; reject unknown forward schema versions."""
    with engine.connect() as connection:
        observed = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
    if observed > CURRENT_SCHEMA:
        raise RuntimeError(
            f"Dashboard SQLite schema v{observed} is newer than supported v{CURRENT_SCHEMA}"
        )
    Base.metadata.create_all(engine)
    # Existing tasks table from P5 has no group_name; SQLAlchemy create_all is
    # not itself a column migration.
    migrate_task_groups()
    with engine.begin() as db:
        db.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, description TEXT NOT NULL, "
            "applied_at TEXT NOT NULL)"
        )
        tables = {
            row[0] for row in db.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing = set().union(*REQUIRED_TABLES.values()) - tables
        if missing:
            raise RuntimeError("Dashboard schema is incomplete; refusing to start")
        task_cols = {
            row[1] for row in db.exec_driver_sql("PRAGMA table_info(tasks)")
        }
        if "group_name" not in task_cols:
            raise RuntimeError("Dashboard P6 group migration did not finish")
        recorded = {
            row[0] for row in db.exec_driver_sql(
                "SELECT version FROM schema_migrations"
            )
        }
        if any(version > CURRENT_SCHEMA for version in recorded):
            raise RuntimeError("Dashboard migration history comes from newer version")
        for version, description in MIGRATIONS.items():
            if version not in recorded:
                db.exec_driver_sql(
                    "INSERT INTO schema_migrations(version,description,applied_at) "
                    "VALUES (?,?,?)",
                    (version, description, now()),
                )
        db.exec_driver_sql(f"PRAGMA user_version={CURRENT_SCHEMA}")
    return schema_status()


def schema_status() -> dict:
    with engine.connect() as db:
        version = db.exec_driver_sql("PRAGMA user_version").scalar_one()
        entries = db.execute(
            text("SELECT version,description,applied_at FROM schema_migrations "
                 "ORDER BY version")
        ).mappings().all()
        return {
            "current": version, "supported": CURRENT_SCHEMA,
            "migrations": [dict(row) for row in entries],
        }

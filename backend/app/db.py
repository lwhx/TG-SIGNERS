import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATA = Path(os.getenv("DATA_DIR", "data")).resolve()
DATA.mkdir(parents=True, exist_ok=True, mode=0o700)


def now():
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(40), default="")
    username: Mapped[str] = mapped_column(String(100), default="")
    user_id: Mapped[str] = mapped_column(String(30), unique=True)
    session: Mapped[str] = mapped_column(Text)  # Fernet encrypted
    api_id: Mapped[int] = mapped_column(Integer)
    api_hash: Mapped[str] = mapped_column(Text)  # encrypted
    proxy: Mapped[str] = mapped_column(Text, default="")  # encrypted
    avatar: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="offline")
    last_checked: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20), default="signer")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cron: Mapped[str] = mapped_column(String(100), default="0 8 * * *")
    timezone: Mapped[str] = mapped_column(String(100), default="Asia/Shanghai")
    delay_min: Mapped[int] = mapped_column(Integer, default=10)
    delay_max: Mapped[int] = mapped_column(Integer, default=300)
    success_pattern: Mapped[str] = mapped_column(String(1000), default="")
    failure_pattern: Mapped[str] = mapped_column(String(1000), default="")
    response_timeout: Mapped[int] = mapped_column(Integer, default=20)
    folder: Mapped[str] = mapped_column(String(100), default="")
    group_name: Mapped[str] = mapped_column(String(60), nullable=False, default="", server_default="")
    config: Mapped[dict] = mapped_column(JSON)  # complete upstream config, no lossy mapping
    next_run: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_run: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_status: Mapped[str] = mapped_column(String(30), default="pending")
    last_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40), default=now)


class TaskVersion(Base):
    __tablename__ = "task_versions"
    __table_args__ = (UniqueConstraint("task_id", "version", name="uq_task_versions_task_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(String(50), default="updated")
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    snapshot: Mapped[dict] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), index=True
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), index=True
    )
    task_name: Mapped[str] = mapped_column(String(100))
    account_name: Mapped[str] = mapped_column(String(100))
    started_at: Mapped[str] = mapped_column(String(40), default=now, index=True)
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    response: Mapped[str] = mapped_column(Text, default="")


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)  # secrets encrypted


engine = create_engine(
    f"sqlite:///{DATA / 'dashboard.sqlite3'}",
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def sqlite_pragmas(connection, _):
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")


def migrate_task_groups():
    """Idempotent additive migration for databases created before P6.

    An existing Telegram folder remains a separate configuration field.
    This runs before any ORM query involving the new group_name column.
    """
    with engine.begin() as db:
        columns = {row[1] for row in db.exec_driver_sql("PRAGMA table_info(tasks)")}
        if "group_name" not in columns:
            db.exec_driver_sql(
                "ALTER TABLE tasks ADD COLUMN group_name VARCHAR(60) NOT NULL DEFAULT ''"
            )


Session = sessionmaker(engine, expire_on_commit=False)


def get_setting(key, default=""):
    with Session() as db:
        item = db.get(Setting, key)
        return item.value if item else default


def set_setting(key, value):
    with Session() as db:
        db.merge(Setting(key=key, value=value))
        db.commit()

"""P8 bounded on-demand performance snapshots; zero Telegram or message-body access."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import resource
import shutil
import sqlite3
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from .db import DATA
from .schema_migrations import schema_status
from .security import rate_limit

router = APIRouter(prefix="/maintenance/performance", tags=["P8 性能基线"])
HISTORY_FILE = DATA / "performance-samples.json"
MAX_SAMPLES = 30
_lock = threading.Lock()


def _db_probe(file: Path) -> dict:
    if not file.is_file():
        return {"present": False, "bytes": 0, "wal_bytes": 0, "p50_ms": None, "p95_ms": None}
    size = file.stat().st_size
    wal = file.with_name(file.name + "-wal")
    durations = []
    with sqlite3.connect(f"file:{file}?mode=ro", uri=True, timeout=0.5) as db:
        for _ in range(8):
            start = time.perf_counter_ns()
            db.execute("SELECT 1").fetchone()
            durations.append((time.perf_counter_ns() - start) / 1_000_000)
        pages, page_size = db.execute("PRAGMA page_count").fetchone()[0], db.execute(
            "PRAGMA page_size"
        ).fetchone()[0]
    durations.sort()
    return {
        "present": True, "bytes": size,
        "wal_bytes": wal.stat().st_size if wal.is_file() else 0,
        "logical_bytes": pages * page_size,
        "p50_ms": round(statistics.median(durations), 3),
        "p95_ms": round(durations[-1], 3),
    }


def measure() -> dict:
    start = time.perf_counter()
    space = shutil.disk_usage(DATA)
    main = _db_probe(DATA / "dashboard.sqlite3")
    relay = _db_probe(DATA / "upstream" / "message_relay.sqlite3")
    snapshot = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "architecture": platform.machine(),
        "dashboard": main, "relay": relay,
        "disk_free_mb": round(space.free / 1048576, 1),
        "disk_total_mb": round(space.total / 1048576, 1),
        "process_rss_peak_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "process_cpu_seconds": round(time.process_time(), 3),
        "probe_ms": round((time.perf_counter() - start) * 1000, 3),
    }
    return snapshot


def history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    if HISTORY_FILE.is_symlink() or HISTORY_FILE.stat().st_size > 256000:
        raise ValueError("性能历史文件无效")
    return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))[-MAX_SAMPLES:]


def record_measurement() -> dict:
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "性能采样正在执行")
    try:
        result = measure()
        prior = history()
        prior.append(result)
        blob = json.dumps(prior[-MAX_SAMPLES:], ensure_ascii=False).encode()
        path = HISTORY_FILE.with_name(".performance-saving-" + str(os.getpid()))
        try:
            with path.open("xb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(blob)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(path, HISTORY_FILE)
        finally:
            path.unlink(missing_ok=True)
        return result
    finally:
        _lock.release()


@router.get("")
async def performance_status():
    return await asyncio.to_thread(lambda: {"current": measure(), "history": history()})


@router.post("/measure")
async def run_performance_measure(request: Request):
    rate_limit(
        "performance-measure:" + (request.client.host if request.client else "local"),
        limit=6, window=60,
    )
    return await asyncio.to_thread(record_measurement)


@router.get("/schema", summary="当前数据库版本及已应用的增量迁移")
def database_schema():
    return schema_status()

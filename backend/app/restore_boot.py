"""Cold-start restore entrypoint. Must run BEFORE importing app.main/security/db.

This module intentionally imports no ORM state or Fernet/JWT runtime secrets.
"""
import os
import sys
from pathlib import Path

from .backup_core import BackupError, apply_pending_restore


def main() -> None:
    data = Path(os.environ.get("DATA_DIR", "/data"))
    try:
        result = apply_pending_restore(data)
    except (BackupError, OSError, ValueError, KeyError) as exc:
        print(
            "P8 cold restore blocked; Dashboard not started: " + type(exc).__name__,
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    if result:
        print("P8 cold restore: " + result["status"], flush=True)


if __name__ == "__main__":
    main()

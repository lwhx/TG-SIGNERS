"""P8 encrypted, bounded, self-contained backups and offline-on-next-start restores.

No application ORM/security imports: restore_boot runs before secrets are loaded.
Never extract arbitrary archive names, follow symlinks, or overwrite live SQLite
while the Dashboard is running. Each SQLite file uses the backup API.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"TGS8\x00"
VERSION = 1
MAX_RAW = 256 * 1024 * 1024
MAX_ENCRYPTED = 100 * 1024 * 1024
MAX_SINGLE = 128 * 1024 * 1024
MAX_FILES = 500
MAX_BACKUPS = 12
BACKUP_PATTERN = re.compile(r"^[a-f0-9]{32}$")
EXCLUDED_ROOT = frozenset({"backups", ".restore-stage", ".restore-previous"})
ESSENTIAL = frozenset({"dashboard.sqlite3", "encryption.key", "jwt.key"})
SQLITE_SUFFIXES = {".sqlite3", ".sqlite", ".db"}


class BackupError(ValueError):
    """Public-safe error without paths, secrets or backup contents."""


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _safe_relative(name: str) -> bool:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("..", ".", "") for part in name.split("/")):
        return False
    return path.parts[0] not in EXCLUDED_ROOT and not name.startswith(".restore-")


def _ensure_path(path: Path, *, directory: bool = False) -> None:
    if path.is_symlink():
        raise BackupError("备份包含符号链接，请在宿主机排查")
    if path.exists():
        status = path.stat()
        if directory and not stat.S_ISDIR(status.st_mode):
            raise BackupError("备份目录不是普通目录")
        if not directory and not stat.S_ISREG(status.st_mode):
            raise BackupError("数据包含不支持的文件类型")


def _valid_id(backup_id: str) -> str:
    if not BACKUP_PATTERN.fullmatch(backup_id):
        raise BackupError("备份标识无效")
    return backup_id


def _derive(secret: str, salt: bytes) -> bytes:
    if not isinstance(secret, str) or len(secret) < 16 or len(secret) > 1024:
        raise BackupError("备份密码长度应为 16～1024 字符")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(secret.encode("utf-8"))


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _ensure_path(path.parent, directory=True)
    descriptor, tmp = tempfile.mkstemp(prefix=".saving-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _backup_dir(data: Path) -> Path:
    directory = data / "backups"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ensure_path(directory, directory=True)
    os.chmod(directory, 0o700)
    return directory


def _eligible_files(data: Path) -> list[str]:
    names = []
    for root, dirs, files in os.walk(data, followlinks=False):
        folder = Path(root)
        dirs[:] = sorted(
            child for child in dirs if child not in EXCLUDED_ROOT
            and not child.startswith(".restore-")
        )
        for dirname in dirs:
            _ensure_path(folder / dirname, directory=True)
        for filename in sorted(files):
            src = folder / filename
            relative = src.relative_to(data).as_posix()
            if (not _safe_relative(relative)
                or filename.endswith(("-wal", "-shm", "-journal"))
                or filename.startswith(".saving-")
                or filename == ".pending_restore.json"
                or filename == ".restore-journal.json"
                or filename == ".restore-last.json"):
                continue
            _ensure_path(src)
            names.append(relative)
            if len(names) > MAX_FILES:
                raise BackupError("数据文件数量超过备份上限（500）")
    if not ESSENTIAL.issubset(names):
        raise BackupError("关键 SQLite 数据库或本地密钥缺失")
    return names


def _sqlite_copy(src: Path) -> bytes:
    with tempfile.TemporaryDirectory(prefix="snapshot-") as scratch:
        dest = Path(scratch) / "snapshot.sqlite3"
        source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
        target = sqlite3.connect(dest)
        try:
            source.backup(target, pages=128, sleep=0.02)
            result = target.execute("PRAGMA quick_check").fetchone()
            if result != ("ok",):
                raise BackupError("数据库完整性检查失败")
        finally:
            target.close()
            source.close()
        return dest.read_bytes()


def _file_data(data: Path, name: str) -> bytes:
    src = data / name
    _ensure_path(src)
    if src.stat().st_size > MAX_SINGLE:
        raise BackupError("单个文件超过备份上限（128 MiB）")
    return _sqlite_copy(src) if src.suffix in SQLITE_SUFFIXES else src.read_bytes()


def _build_zip(data: Path) -> tuple[bytes, dict]:
    if os.getenv("ENCRYPTION_KEY") or os.getenv("JWT_SECRET"):
        raise BackupError("使用外部密钥变量时不能创建可独立恢复的完整备份")
    files = {}
    payloads = {}
    total = 0
    for name in _eligible_files(data):
        contents = _file_data(data, name)
        total += len(contents)
        if total > MAX_RAW:
            raise BackupError("备份原始数据超过上限（256 MiB）")
        files[name] = {"size": len(contents), "sha256": _hash(contents)}
        payloads[name] = contents
    manifest = {
        "format": VERSION, "created_at": _stamp(),
        "files": files, "total_size": total,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        for name in sorted(payloads):
            archive.writestr("data/" + name, payloads[name])
    return output.getvalue(), manifest


def _valid_manifest(manifest: dict, members: dict[str, bytes]) -> None:
    if not isinstance(manifest, dict) or manifest.get("format") != VERSION:
        raise BackupError("不支持的备份格式版本")
    files = manifest.get("files")
    if not isinstance(files, dict) or not ESSENTIAL.issubset(files):
        raise BackupError("备份缺少必要数据或密钥")
    if len(files) > MAX_FILES:
        raise BackupError("备份文件数量超限")
    if set(files) != set(members):
        raise BackupError("备份目录或清单不一致")
    total = 0
    for name, metadata in files.items():
        if not _safe_relative(name) or not isinstance(metadata, dict):
            raise BackupError("备份包含非法路径")
        payload = members[name]
        total += len(payload)
        if (
            len(payload) > MAX_SINGLE
            or metadata.get("size") != len(payload)
            or metadata.get("sha256") != _hash(payload)
        ):
            raise BackupError("备份文件校验失败")
        if total > MAX_RAW:
            raise BackupError("备份解压大小超过上限")
    if manifest.get("total_size") != total:
        raise BackupError("备份总大小校验失败")
    # Validate SQLite bytes in the snapshot before making a restore plan.
    with tempfile.TemporaryDirectory(prefix="verify-") as scratch:
        for index, (name, payload) in enumerate(members.items()):
            if Path(name).suffix not in SQLITE_SUFFIXES:
                continue
            file = Path(scratch) / f"db-{index}.sqlite3"
            file.write_bytes(payload)
            try:
                with sqlite3.connect(f"file:{file}?mode=ro", uri=True) as connection:
                    if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                        raise BackupError("备份 SQLite 完整性检查失败")
            except sqlite3.DatabaseError as exc:
                raise BackupError("备份中的数据库无法读取") from exc
    if not members["encryption.key"].strip() or not members["jwt.key"].strip():
        raise BackupError("备份中的密钥无效")


def _decode(blob: bytes, secret: str) -> tuple[dict, dict[str, bytes]]:
    if not blob.startswith(MAGIC) or len(blob) <= len(MAGIC) + 28 + 16:
        raise BackupError("备份头部格式无效")
    if len(blob) > MAX_ENCRYPTED:
        raise BackupError("备份文件超过允许大小")
    salt = blob[len(MAGIC):len(MAGIC)+16]
    nonce = blob[len(MAGIC)+16:len(MAGIC)+28]
    try:
        zipped = AESGCM(_derive(secret, salt)).decrypt(
            nonce, blob[len(MAGIC)+28:], MAGIC
        )
    except (InvalidTag, ValueError) as exc:
        raise BackupError("备份密码错误或文件已损坏") from exc
    members = {}
    try:
        with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
            infos = archive.infolist()
            if len(infos) < 4 or len(infos) > MAX_FILES + 1:
                raise BackupError("备份文件数不正确")
            names = [entry.filename for entry in infos]
            if len(set(names)) != len(names) or "manifest.json" not in names:
                raise BackupError("备份包含重复文件或缺少清单")
            if any(
                name != "manifest.json" and
                (not name.startswith("data/") or not _safe_relative(name[5:]))
                for name in names
            ):
                raise BackupError("备份包含非法条目")
            if sum(info.file_size for info in infos) > MAX_RAW + 256000:
                raise BackupError("备份解压大小异常")
            for info in infos:
                if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                    raise BackupError("备份含不支持的链接或目录")
                if info.file_size > MAX_SINGLE and info.filename != "manifest.json":
                    raise BackupError("备份文件过大")
            manifest = json.loads(archive.read("manifest.json", pwd=None))
            for info in infos:
                if info.filename != "manifest.json":
                    members[info.filename[5:]] = archive.read(info)
    except (zipfile.BadZipFile, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError("备份 ZIP 文件损坏") from exc
    _valid_manifest(manifest, members)
    return manifest, members


def create_backup(data: Path, secret: str) -> dict:
    data = data.resolve()
    secret_key = _derive(secret, os.urandom(16))  # validate password before file I/O
    del secret_key
    directory = _backup_dir(data)
    if len(list(directory.glob("*.tgsb"))) >= MAX_BACKUPS:
        raise BackupError("备份数量已达上限（12），请先下载并删除旧备份")
    zipped, manifest = _build_zip(data)
    if len(zipped) > MAX_ENCRYPTED - 64:
        raise BackupError("压缩后备份过大")
    salt, nonce = os.urandom(16), os.urandom(12)
    blob = MAGIC + salt + nonce + AESGCM(_derive(secret, salt)).encrypt(
        nonce, zipped, MAGIC
    )
    name = uuid.uuid4().hex
    _atomic_bytes(directory / f"{name}.tgsb", blob)
    return {
        "id": name, "created_at": manifest["created_at"],
        "files": len(manifest["files"]), "size": len(blob),
    }


def import_backup(data: Path, blob: bytes, secret: str) -> dict:
    """Import an encrypted archive after full integrity and password checks."""
    data = data.resolve()
    if len(blob) > MAX_ENCRYPTED:
        raise BackupError("上传的备份文件超过 100 MiB")
    manifest, _ = _decode(blob, secret)
    directory = _backup_dir(data)
    if len(list(directory.glob("*.tgsb"))) >= MAX_BACKUPS:
        raise BackupError("备份数量已达上限（12），请先删除旧备份")
    name = uuid.uuid4().hex
    _atomic_bytes(directory / f"{name}.tgsb", blob)
    return {
        "id": name, "created_at": manifest["created_at"],
        "files": len(manifest["files"]), "size": len(blob),
    }


def backup_path(data: Path, backup_id: str) -> Path:
    file = _backup_dir(data) / (_valid_id(backup_id) + ".tgsb")
    _ensure_path(file)
    if not file.is_file():
        raise BackupError("备份不存在")
    return file


def list_backups(data: Path) -> list[dict]:
    result = []
    for file in _backup_dir(data).glob("*.tgsb"):
        if not BACKUP_PATTERN.fullmatch(file.stem):
            continue
        _ensure_path(file)
        info = file.stat()
        result.append({
            "id": file.stem, "size": info.st_size,
            "created_at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
        })
    return sorted(result, key=lambda item: item["created_at"], reverse=True)


def verify_backup(data: Path, backup_id: str, secret: str) -> dict:
    manifest, _ = _decode(backup_path(data, backup_id).read_bytes(), secret)
    return {
        "valid": True, "files": len(manifest["files"]),
        "created_at": manifest["created_at"],
        "total_size": manifest["total_size"],
    }


def _pending_path(data: Path) -> Path:
    return data / ".pending_restore.json"


def queue_restore(data: Path, backup_id: str, secret: str) -> dict:
    data = data.resolve()
    if os.getenv("ENCRYPTION_KEY") or os.getenv("JWT_SECRET"):
        raise BackupError("使用外部密钥变量时禁止在面板中恢复")
    _valid_id(backup_id)
    if _pending_path(data).exists() or (data / ".restore-journal.json").exists():
        raise BackupError("已有等待执行或未清理的恢复任务")
    manifest, files = _decode(backup_path(data, backup_id).read_bytes(), secret)
    stage = data / ".restore-stage" / backup_id
    if stage.exists():
        raise BackupError("恢复暂存目录已存在")
    stage.mkdir(parents=True, mode=0o700)
    try:
        for name, payload in files.items():
            target = stage / "data" / name
            _atomic_bytes(target, payload)
        _atomic_bytes(
            stage / "manifest.json",
            json.dumps(manifest, sort_keys=True).encode("utf-8"),
        )
        pending = {"id": backup_id, "created_at": _stamp()}
        _atomic_bytes(
            _pending_path(data), json.dumps(pending).encode("utf-8")
        )
    except BaseException:
        shutil.rmtree(stage)
        raise
    return {
        "pending": True, "id": backup_id, "files": len(files),
        "message": "备份已校验并暂存；重启容器后在应用加载前执行恢复",
    }


def restore_status(data: Path) -> dict:
    pending = _pending_path(data)
    last = data / ".restore-last.json"
    return {
        "pending": json.loads(pending.read_text()) if pending.exists() else None,
        "last": json.loads(last.read_text()) if last.exists() else None,
    }


def _journal(data: Path, value: dict) -> None:
    _atomic_bytes(
        data / ".restore-journal.json",
        json.dumps(value, sort_keys=True).encode("utf-8"),
    )


def _undo(data: Path, journal: dict) -> None:
    old = data / ".restore-previous" / _valid_id(journal["id"])
    for name in journal["replace"]:
        if not _safe_relative(name):
            raise BackupError("恢复回滚路径不合法")
        destination = data / name
        saved = old / name
        if saved.exists():
            _ensure_path(saved)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, destination)
        elif name not in journal["existing"]:
            destination.unlink(missing_ok=True)
        else:
            raise BackupError("恢复回滚文件丢失；为避免损坏已停止启动")


def apply_pending_restore(data: Path) -> dict | None:
    """Entrypoint-only: apply on a cold process, before importing app.security."""
    data = data.resolve()
    pending_file = _pending_path(data)
    journal_file = data / ".restore-journal.json"
    if journal_file.exists():
        journal = json.loads(journal_file.read_text())
        if journal.get("state") == "applying":
            _undo(data, journal)
            _atomic_bytes(
                data / ".restore-last.json",
                json.dumps({"id": journal["id"], "status": "rolled_back", "time": _stamp()}).encode(),
            )
            pending_file.unlink(missing_ok=True)
            journal_file.unlink()
            return {"status": "rolled_back"}
        if journal.get("state") == "completed":
            pending_file.unlink(missing_ok=True)
            journal_file.unlink()
            return {"status": "completed"}
        raise BackupError("未知恢复日志状态，禁止继续运行")
    if not pending_file.exists():
        return None
    pending = json.loads(pending_file.read_text())
    backup_id = _valid_id(pending["id"])
    stage = data / ".restore-stage" / backup_id
    manifest = json.loads((stage / "manifest.json").read_text())
    files = {
        name: (stage / "data" / name).read_bytes()
        for name in manifest["files"] if _safe_relative(name)
    }
    _valid_manifest(manifest, files)
    replace = sorted(set(files) | {
        dbfile + ext for dbfile in files
        if Path(dbfile).suffix in SQLITE_SUFFIXES for ext in ("-wal", "-shm")
    })
    previous = data / ".restore-previous" / backup_id
    if previous.exists():
        raise BackupError("恢复回退快照已存在；请人工检查")
    previous.mkdir(parents=True, mode=0o700)
    existing = []
    for name in replace:
        live = data / name
        if live.exists():
            _ensure_path(live)
            existing.append(name)
            destination = previous / name
            destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            shutil.copy2(live, destination)
    journal = {
        "id": backup_id, "state": "applying",
        "replace": replace, "existing": existing,
    }
    _journal(data, journal)
    try:
        for name in replace:
            target = data / name
            target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            if name in files:
                os.replace(stage / "data" / name, target)
            else:
                target.unlink(missing_ok=True)  # never replay old SQLite WAL
        _atomic_bytes(
            data / ".restore-last.json",
            json.dumps({"id": backup_id, "status": "completed", "time": _stamp()}).encode(),
        )
        _journal(data, {**journal, "state": "completed"})
        pending_file.unlink(missing_ok=True)
        journal_file.unlink(missing_ok=True)
        return {"status": "completed", "id": backup_id}
    except BaseException:
        _undo(data, journal)
        _atomic_bytes(
            data / ".restore-last.json",
            json.dumps({"id": backup_id, "status": "rolled_back", "time": _stamp()}).encode(),
        )
        pending_file.unlink(missing_ok=True)
        journal_file.unlink(missing_ok=True)
        raise

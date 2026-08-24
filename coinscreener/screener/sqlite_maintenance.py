import os
import sqlite3
from datetime import datetime
from pathlib import Path


BACKUP_PREFIX = 'coin-screener'


def ensure_sqlite_database(database_config):
    engine = database_config.get('ENGINE', '')
    if engine != 'django.db.backends.sqlite3':
        raise ValueError('이 명령은 SQLite 데이터베이스에서만 사용할 수 있습니다.')

    database_path = Path(database_config['NAME']).resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f'SQLite 데이터베이스가 없습니다: {database_path}')
    return database_path


def verify_sqlite_database(database_path):
    database_path = Path(database_path).resolve()
    uri = database_path.as_uri() + '?mode=ro'
    connection = None
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=30)
        result = connection.execute('PRAGMA integrity_check').fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError(
            f'SQLite 파일을 열거나 검사할 수 없습니다: {exc}'
        ) from exc
    finally:
        if connection is not None:
            connection.close()

    if not result or result[0] != 'ok':
        detail = result[0] if result else 'no result'
        raise RuntimeError(f'SQLite 무결성 검사 실패: {detail}')


def create_sqlite_snapshot(source_path, destination_dir, label=BACKUP_PREFIX):
    source_path = Path(source_path).resolve()
    destination_dir = Path(destination_dir).resolve()
    destination_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    final_path = destination_dir / f'{label}-{timestamp}.sqlite3'
    temporary_path = destination_dir / f'.{final_path.name}.tmp'

    source = None
    destination = None
    try:
        source = sqlite3.connect(str(source_path), timeout=30)
        destination = sqlite3.connect(str(temporary_path), timeout=30)
        source.backup(destination)
    except sqlite3.Error as exc:
        temporary_path.unlink(missing_ok=True)
        raise RuntimeError(f'SQLite 백업 생성 실패: {exc}') from exc
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()

    try:
        verify_sqlite_database(temporary_path)
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, final_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return final_path


def prune_sqlite_snapshots(destination_dir, keep, label=BACKUP_PREFIX):
    destination_dir = Path(destination_dir).resolve()
    snapshots = sorted(
        destination_dir.glob(f'{label}-*.sqlite3'),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed = snapshots[keep:]
    for path in removed:
        path.unlink()
    return removed

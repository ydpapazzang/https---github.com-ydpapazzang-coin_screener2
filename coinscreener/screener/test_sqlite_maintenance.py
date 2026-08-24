import io
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from .apps import configure_sqlite_connection
from .management.commands.backup_sqlite import Command as BackupCommand
from .management.commands.restore_sqlite import Command as RestoreCommand
from .sqlite_maintenance import verify_sqlite_database


class SQLiteMaintenanceTestCase(SimpleTestCase):
    def _create_database(self, path, value):
        connection = sqlite3.connect(path)
        try:
            connection.execute('CREATE TABLE sample (value TEXT NOT NULL)')
            connection.execute(
                'INSERT INTO sample (value) VALUES (?)',
                (value,),
            )
            connection.commit()
        finally:
            connection.close()

    def _read_value(self, path):
        connection = sqlite3.connect(path)
        try:
            return connection.execute(
                'SELECT value FROM sample'
            ).fetchone()[0]
        finally:
            connection.close()

    def _database_settings(self, path):
        return {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': Path(path),
                'OPTIONS': {'timeout': 30},
            }
        }

    def test_backup_is_consistent_and_retention_is_applied(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / 'db.sqlite3'
            backups = root / 'backups'
            self._create_database(database, 'before')

            with patch.object(
                settings,
                'DATABASES',
                self._database_settings(database),
            ):
                BackupCommand(stdout=io.StringIO()).handle(
                    destination=str(backups),
                    keep=1,
                )
                connection = sqlite3.connect(database)
                try:
                    connection.execute(
                        'UPDATE sample SET value = ?',
                        ('after',),
                    )
                    connection.commit()
                finally:
                    connection.close()
                BackupCommand(stdout=io.StringIO()).handle(
                    destination=str(backups),
                    keep=1,
                )

            snapshots = list(backups.glob('coin-screener-*.sqlite3'))
            self.assertEqual(len(snapshots), 1)
            verify_sqlite_database(snapshots[0])
            self.assertEqual(self._read_value(snapshots[0]), 'after')

    def test_restore_requires_explicit_services_stopped_confirmation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / 'db.sqlite3'
            backup = root / 'backup.sqlite3'
            self._create_database(database, 'current')
            self._create_database(backup, 'backup')

            with patch.object(
                settings,
                'DATABASES',
                self._database_settings(database),
            ):
                with self.assertRaisesRegex(
                    CommandError,
                    'confirm-services-stopped',
                ):
                    RestoreCommand(stdout=io.StringIO()).handle(
                        backup_path=str(backup),
                        confirm_services_stopped=False,
                        backup_dir=str(root / 'backups'),
                    )

    def test_restore_rejects_corrupt_backup_without_replacing_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / 'db.sqlite3'
            corrupt = root / 'corrupt.sqlite3'
            self._create_database(database, 'current')
            corrupt.write_bytes(b'not a sqlite database')

            with patch.object(
                settings,
                'DATABASES',
                self._database_settings(database),
            ):
                with self.assertRaisesRegex(
                    CommandError,
                    '열거나 검사할 수 없습니다',
                ):
                    RestoreCommand(stdout=io.StringIO()).handle(
                        backup_path=str(corrupt),
                        confirm_services_stopped=True,
                        backup_dir=str(root / 'backups'),
                    )

            self.assertEqual(self._read_value(database), 'current')

    def test_restore_replaces_database_and_keeps_safety_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / 'db.sqlite3'
            backup = root / 'restore-source.sqlite3'
            backups = root / 'backups'
            self._create_database(database, 'current')
            self._create_database(backup, 'restored')

            with patch.object(
                settings,
                'DATABASES',
                self._database_settings(database),
            ), patch(
                'coinscreener.screener.management.commands.restore_sqlite.connections.close_all'
            ) as mock_close:
                RestoreCommand(stdout=io.StringIO()).handle(
                    backup_path=str(backup),
                    confirm_services_stopped=True,
                    backup_dir=str(backups),
                )

            self.assertEqual(self._read_value(database), 'restored')
            safety_snapshots = list(
                backups.glob('pre-restore-*.sqlite3')
            )
            self.assertEqual(len(safety_snapshots), 1)
            self.assertEqual(
                self._read_value(safety_snapshots[0]),
                'current',
            )
            mock_close.assert_called_once_with()

    def test_sqlite_connection_pragmas_are_configured(self):
        cursor = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = cursor
        connection = MagicMock(vendor='sqlite')
        connection.cursor.return_value = context

        configure_sqlite_connection(
            sender=None,
            connection=connection,
        )

        self.assertEqual(
            [call.args[0] for call in cursor.execute.call_args_list],
            [
                'PRAGMA busy_timeout=30000',
                'PRAGMA journal_mode=WAL',
                'PRAGMA synchronous=NORMAL',
            ],
        )

    def test_non_sqlite_connection_is_ignored(self):
        connection = MagicMock(vendor='postgresql')

        configure_sqlite_connection(
            sender=None,
            connection=connection,
        )

        connection.cursor.assert_not_called()

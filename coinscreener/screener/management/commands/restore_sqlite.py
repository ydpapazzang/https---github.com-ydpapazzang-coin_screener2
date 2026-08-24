import os
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from coinscreener.screener.sqlite_maintenance import (
    create_sqlite_snapshot,
    ensure_sqlite_database,
    verify_sqlite_database,
)


class Command(BaseCommand):
    help = '검증된 SQLite 백업을 복구하고 기존 DB의 복구 전 스냅샷을 보존합니다.'

    def add_arguments(self, parser):
        parser.add_argument('backup_path', help='복구할 .sqlite3 백업 파일')
        parser.add_argument(
            '--confirm-services-stopped',
            action='store_true',
            help='coinscreener/upbit-crawler/kospi-crawler 중지를 확인합니다.',
        )
        parser.add_argument(
            '--backup-dir',
            default=str(settings.BASE_DIR / 'backups'),
            help='복구 전 스냅샷을 저장할 디렉터리',
        )

    def handle(self, *args, **options):
        if not options['confirm_services_stopped']:
            raise CommandError(
                '먼저 coinscreener, upbit-crawler, kospi-crawler 서비스를 '
                '중지한 뒤 --confirm-services-stopped를 지정하세요.'
            )

        backup_path = Path(options['backup_path']).resolve()
        if not backup_path.is_file():
            raise CommandError(f'백업 파일이 없습니다: {backup_path}')

        try:
            database_path = ensure_sqlite_database(
                settings.DATABASES['default']
            )
            if backup_path == database_path:
                raise ValueError('현재 DB 파일 자체를 복구 원본으로 사용할 수 없습니다.')

            verify_sqlite_database(backup_path)
            safety_snapshot = create_sqlite_snapshot(
                database_path,
                Path(options['backup_dir']),
                label='pre-restore',
            )

            temporary_path = database_path.with_name(
                f'.{database_path.name}.restore.tmp'
            )
            shutil.copy2(backup_path, temporary_path)
            verify_sqlite_database(temporary_path)
            os.chmod(temporary_path, database_path.stat().st_mode & 0o777)

            connections.close_all()
            for suffix in ('-wal', '-shm', '-journal'):
                database_path.with_name(
                    database_path.name + suffix
                ).unlink(missing_ok=True)
            os.replace(temporary_path, database_path)
            verify_sqlite_database(database_path)
        except (OSError, ValueError, RuntimeError) as exc:
            temporary = locals().get('temporary_path')
            if temporary:
                temporary.unlink(missing_ok=True)
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(
            f'SQLite restore completed: {database_path}'
        ))
        self.stdout.write(
            f'Pre-restore safety snapshot: {safety_snapshot}'
        )
        self.stdout.write(self.style.WARNING(
            '이제 migrate를 실행한 뒤 중지했던 서비스를 다시 시작하세요.'
        ))

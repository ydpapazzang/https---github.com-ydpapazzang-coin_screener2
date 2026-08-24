from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from coinscreener.screener.sqlite_maintenance import (
    create_sqlite_snapshot,
    ensure_sqlite_database,
    prune_sqlite_snapshots,
)


class Command(BaseCommand):
    help = '실행 중인 SQLite DB의 일관된 스냅샷을 만들고 오래된 백업을 정리합니다.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--destination',
            default=str(settings.BASE_DIR / 'backups'),
            help='백업 저장 디렉터리',
        )
        parser.add_argument(
            '--keep',
            type=int,
            default=14,
            help='보관할 최근 백업 개수(기본 14개)',
        )

    def handle(self, *args, **options):
        keep = options['keep']
        if keep < 1:
            raise CommandError('--keep는 1 이상이어야 합니다.')

        try:
            database_path = ensure_sqlite_database(
                settings.DATABASES['default']
            )
            destination = Path(options['destination'])
            snapshot = create_sqlite_snapshot(
                database_path,
                destination,
            )
            removed = prune_sqlite_snapshots(destination, keep)
        except (OSError, ValueError, RuntimeError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(
            f'SQLite backup completed: {snapshot}'
        ))
        self.stdout.write(
            f'Backup retention: keep={keep}, removed={len(removed)}'
        )

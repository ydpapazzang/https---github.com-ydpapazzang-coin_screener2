import json

from django.core.management.base import BaseCommand

from coinscreener.screener.scheduled_scans import run_scheduled_scans


class Command(BaseCommand):
    help = '현재 KST 슬롯의 예약 전략을 Gunicorn과 분리된 프로세스에서 실행합니다.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true')

    def handle(self, *args, **options):
        result = run_scheduled_scans(
            force=options['force'],
            output=self.stdout.write,
        )
        self.stdout.write(json.dumps(result, ensure_ascii=False, default=str))
        if not result['ok']:
            self.stderr.write(self.style.WARNING('일부 예약 스캔이 실패했습니다.'))


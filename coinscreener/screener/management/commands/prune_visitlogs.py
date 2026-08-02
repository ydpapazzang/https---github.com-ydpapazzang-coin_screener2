from django.core.management.base import BaseCommand
from django.utils import timezone
from coinscreener.screener.models import VisitLog


class Command(BaseCommand):
    help = 'N일(기본 30일)보다 오래된 방문기록(VisitLog)을 삭제해 DB 크기를 유지합니다.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30, help='보관 일수 (기본 30일)')

    def handle(self, *args, **options):
        days = options['days']
        cutoff = timezone.now() - timezone.timedelta(days=days)
        deleted, _ = VisitLog.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write(self.style.SUCCESS(f'{days}일 이전 방문기록 {deleted}건 삭제 완료.'))

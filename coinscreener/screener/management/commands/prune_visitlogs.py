from django.core.management.base import BaseCommand
from django.utils import timezone
from coinscreener.screener.models import ScanLease, ScanUsage, VisitLog


class Command(BaseCommand):
    help = '오래된 방문·스캔 사용량과 만료 실행권을 삭제해 DB 크기를 유지합니다.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30, help='보관 일수 (기본 30일)')

    def handle(self, *args, **options):
        days = options['days']
        cutoff = timezone.now() - timezone.timedelta(days=days)
        visit_deleted, _ = VisitLog.objects.filter(created_at__lt=cutoff).delete()
        usage_deleted, _ = ScanUsage.objects.filter(
            date__lt=timezone.localdate() - timezone.timedelta(days=days)
        ).delete()
        lease_deleted, _ = ScanLease.objects.filter(
            expires_at__lt=timezone.now()
        ).delete()
        self.stdout.write(self.style.SUCCESS(
            f'{days}일 이전 방문기록 {visit_deleted}건, '
            f'스캔 사용량 {usage_deleted}건, 만료 실행권 {lease_deleted}건 삭제 완료.'
        ))


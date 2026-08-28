from django.core.management.base import BaseCommand

from coinscreener.screener.models import Strategy
from coinscreener.screener.sample_cleanup import prune_public_samples


class Command(BaseCommand):
    help = '개인 전략은 보존하고 지정한 공용 예시 전략 하나만 남깁니다.'

    def add_arguments(self, parser):
        parser.add_argument('--keep', default='월봉이')

    def handle(self, *args, **options):
        keep_name = options['keep'].strip()
        if not keep_name:
            self.stderr.write(self.style.ERROR('보존할 예시 전략명이 필요합니다.'))
            return
        target_ids, deleted_count = prune_public_samples(
            Strategy, keep_name=keep_name
        )
        self.stdout.write(self.style.SUCCESS(
            f"공용 예시 정리 완료: 보존='{keep_name}', "
            f"삭제 대상 전략={len(target_ids)}개, 관계 객체 포함 삭제={deleted_count}개"
        ))


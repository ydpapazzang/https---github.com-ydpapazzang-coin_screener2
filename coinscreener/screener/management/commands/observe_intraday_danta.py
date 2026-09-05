from django.core.management.base import BaseCommand
from django.utils import timezone

from coinscreener.screener.engine import get_ohlcv_with_retry
from coinscreener.screener.intraday_danta import (
    INTRADAY_DANTA_STRATEGY_VERSION,
    SignalRejected,
    build_intraday_signal,
)
from coinscreener.screener.models import IntradayObservation
from coinscreener.screener.views.scan_views import _get_tickers


class Command(BaseCommand):
    help = 'Record cache-backed intraday danta signals in observation mode only.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--limit', type=int, default=50)

    def handle(self, *args, **options):
        now = timezone.localtime(timezone.now())
        if not options['force'] and not 9 <= now.hour < 23:
            self.stdout.write('Outside intraday observation window (09:00-22:59 KST).')
            return

        # A 15-minute bucket prevents duplicate records if the timer is retried.
        slot = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        tickers = _get_tickers('upbit', max(1, min(options['limit'], 50)))
        created = 0
        rejected = 0
        for item in tickers:
            ticker = item['ticker']
            if IntradayObservation.objects.filter(
                ticker=ticker,
                strategy_version=INTRADAY_DANTA_STRATEGY_VERSION,
                detected_at=slot,
            ).exists():
                continue
            frame = get_ohlcv_with_retry(
                ticker, 'minute15', count=80, retries=1, delay=0.1,
                exchange='upbit', persist_db=False, cache_only=True,
            )
            try:
                signal = build_intraday_signal(ticker, frame, item.get('current_price'))
            except SignalRejected:
                rejected += 1
                continue
            IntradayObservation.objects.create(
                detected_at=slot,
                ticker=ticker,
                name=item.get('name', ticker.replace('KRW-', '')),
                entry_price=signal['entry_price'],
                target_1_price=signal['target_1_price'],
                target_2_price=signal['target_2_price'],
                stop_loss=signal['stop_loss'],
                reason=signal['reason'],
                strategy_version=signal['strategy_version'],
            )
            created += 1
        self.stdout.write(
            f'[INTRADAY_OBSERVE] slot={slot:%Y-%m-%d %H:%M KST} '
            f'candidates={len(tickers)} created={created} rejected={rejected}'
        )


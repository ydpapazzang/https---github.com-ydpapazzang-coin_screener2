import math

import pyupbit
import requests
from django.core.management.base import BaseCommand
from django.utils import timezone

from coinscreener.screener.daily_picks import is_stablecoin_ticker
from coinscreener.screener.danta_dual_timeframe import (
    DUAL_DANTA_STRATEGY_VERSION, SignalRejected, build_pullback_signal,
)
from coinscreener.screener.engine import get_ohlcv_with_retry
from coinscreener.screener.models import DailyRecommendation
from coinscreener.screener.recommendation_versioning import json_number, recommendation_snapshot
from coinscreener.screener.telegram import send_message


UNIVERSE_LIMIT = 30
MAX_NEW_SIGNALS_PER_RUN = 3


class Command(BaseCommand):
    help = '마감된 1H/5M 봉으로 모멘텀-눌림목 단타 신호를 생성합니다.'

    def _top_liquid_tickers(self):
        markets = pyupbit.get_tickers(fiat='KRW') or []
        rows = []
        for start in range(0, len(markets), 100):
            response = requests.get(
                'https://api.upbit.com/v1/ticker',
                params={'markets': ','.join(markets[start:start + 100])}, timeout=6,
            ).json()
            if not isinstance(response, list):
                continue
            rows.extend(response)
        rows = [
            row for row in rows
            if row.get('market') and not is_stablecoin_ticker(row['market'])
        ]
        rows.sort(key=lambda row: float(row.get('acc_trade_price_24h') or 0), reverse=True)
        return rows[:UNIVERSE_LIMIT]

    def handle(self, *args, **options):
        now = timezone.now()
        today = timezone.localtime(now).date()
        try:
            candidates = self._top_liquid_tickers()
        except Exception as exc:
            self.stderr.write(f'[DANTA_DUAL] universe error: {exc}')
            return

        created = []
        for row in candidates:
            ticker = row['market']
            if DailyRecommendation.objects.filter(
                date=today, coin_ticker=ticker,
                strategy_version=DUAL_DANTA_STRATEGY_VERSION,
            ).exists():
                continue
            try:
                hourly = get_ohlcv_with_retry(ticker, 'minute60', count=260, retries=1, delay=0.1, persist_db=False)
                five_minute = get_ohlcv_with_retry(ticker, 'minute5', count=90, retries=1, delay=0.1, persist_db=False)
                signal = build_pullback_signal(hourly, five_minute)
            except SignalRejected:
                continue
            except Exception as exc:
                self.stderr.write(f'[DANTA_DUAL] {ticker} data error: {exc}')
                continue

            parameters = {
                'timeframes': {'trend': 'minute60', 'entry': 'minute5'},
                'ma200': 200, 'ichimoku': [9, 26, 52, 26],
                'bb': [20, 2.0], 'volume_ma': 20,
                'volume_expansion_multiplier': 2.0,
                'setup_window_minutes': 30,
                'max_stop_loss_pct': 1.0,
                'minimum_risk_reward': 1.8,
                'kijun_distance_pct': json_number(signal['kijun_distance_pct']),
                'support_level': json_number(signal['support_level']),
                'support_confirmed': True,
                'target_2_rule': '1H kijun break or tenkan/kijun dead cross',
            }
            snapshot = recommendation_snapshot(
                DUAL_DANTA_STRATEGY_VERSION, parameters,
                {'label': '1h_long_bias', 'hourly_close': json_number(signal['hourly_close'])}, now,
            )
            recommendation = DailyRecommendation.objects.create(
                date=today, trade_type='danta', coin_ticker=ticker,
                coin_name=ticker.replace('KRW-', ''),
                entry_price=signal['entry_price'], target_price=signal['target_1_price'],
                stop_loss=signal['stop_loss'], initial_stop_loss=signal['stop_loss'],
                k_value=0, status='active', entered_at=now, last_checked_at=now,
                highest_price=signal['entry_price'], lowest_price=signal['entry_price'],
                reason=(
                    '1H 장기 추세·일목 필터 통과, 5M 거래량 2배/볼린저 상단 분출 후 '
                    f'{signal["setup_candles_ago"] * 5}분 내 첫 눌림목 지지 확인. '
                    f'1H 기준선 이격 {signal["kijun_distance_pct"]:.2f}%, '
                    f'지지선 {signal["support_level"]:,.0f}, 손익비 1:{signal["risk_reward"]:.2f}.'
                ),
                **snapshot,
            )
            created.append(recommendation)
            if len(created) >= MAX_NEW_SIGNALS_PER_RUN:
                break

        if not created:
            self.stdout.write('[DANTA_DUAL] no completed-candle pullback signal')
            return
        lines = ['🔥 <b>1H/5M 눌림목 단타 신호</b>']
        for rec in created:
            lines.append(
                f'• <b>{rec.coin_name}</b> {rec.coin_ticker}\n'
                f'  진입 {rec.entry_price:,.0f} / TP1 {rec.target_price:,.0f} / SL {rec.stop_loss:,.0f}'
            )
        lines.append("\n👉 <a href='https://woniiscreener.duckdns.org/danta/'>단타 탭 열기</a>")
        result = send_message('\n'.join(lines))
        self.stdout.write(self.style.SUCCESS(
            f'[DANTA_DUAL] created={len(created)} telegram={result.get("ok", False)}'
        ))


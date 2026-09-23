from django.core.management.base import BaseCommand
import requests
import math
from coinscreener.screener.models import MarketData


class Command(BaseCommand):
    help = '업비트 마켓 데이터(현재가, 거래량, 거래대금)를 DB에 업데이트합니다.'

    def handle(self, *args, **options):
        self.stdout.write("Starting MarketData update (Upbit only)...")
        try:
            self._update_upbit_data()
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error fetching Upbit: {e}"))
        self.stdout.write(self.style.SUCCESS("Successfully updated MarketData!"))

    def _update_upbit_data(self):
        from django.db import transaction

        # 한글 종목명 조회
        market_all_url = 'https://api.upbit.com/v1/market/all'
        market_all_data = requests.get(market_all_url).json()
        name_dict = {
            item['market']: item['korean_name']
            for item in market_all_data
            if item['market'].startswith('KRW-')
        }

        tickers = list(name_dict.keys())
        chunk_size = 100

        is_empty = not MarketData.objects.filter(exchange='upbit').exists()
        objects_to_create = []

        with transaction.atomic():
            for i in range(0, len(tickers), chunk_size):
                chunk = tickers[i:i + chunk_size]
                markets = ','.join(chunk)
                url = f'https://api.upbit.com/v1/ticker?markets={markets}'
                resp = requests.get(url).json()

                for item in resp:
                    ticker = item['market']
                    name = name_dict.get(ticker, ticker)
                    close_price = float(item.get('trade_price', 0))
                    volume = float(item.get('acc_trade_volume_24h', 0))
                    amount = float(item.get('acc_trade_price_24h', 0))

                    if is_empty:
                        objects_to_create.append(MarketData(
                            exchange='upbit',
                            ticker=ticker,
                            name=name,
                            close_price=close_price,
                            volume=volume,
                            amount=amount,
                            market_cap=None,
                        ))
                    else:
                        MarketData.objects.update_or_create(
                            exchange='upbit',
                            ticker=ticker,
                            defaults={
                                'name': name,
                                'close_price': close_price,
                                'volume': volume,
                                'amount': amount,
                                'market_cap': None,
                            }
                        )

            if is_empty and objects_to_create:
                MarketData.objects.bulk_create(objects_to_create, batch_size=500)

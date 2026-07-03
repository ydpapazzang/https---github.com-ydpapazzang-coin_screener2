from django.core.management.base import BaseCommand
import FinanceDataReader as fdr
import requests
import math
from coinscreener.screener.models import MarketData

class Command(BaseCommand):
    help = 'Fetches and updates market data (price, volume, amount, market cap) into the database.'

    def handle(self, *args, **options):
        self.stdout.write("Starting MarketData update...")
        
        # 1. Update KOSPI
        self.stdout.write("Updating KOSPI...")
        try:
            kospi_df = fdr.StockListing('KOSPI')
            self._update_fdr_data('kospi', kospi_df)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error fetching KOSPI: {e}"))
            
        # 2. Update ETF
        self.stdout.write("Updating ETF/KR...")
        try:
            etf_df = fdr.StockListing('ETF/KR')
            self._update_fdr_data('kospi', etf_df) # store as kospi to match existing logic if user searches for kospi
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error fetching ETF: {e}"))
            
        # 3. Update Upbit
        self.stdout.write("Updating Upbit...")
        try:
            self._update_upbit_data()
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error fetching Upbit: {e}"))
            
        self.stdout.write(self.style.SUCCESS("Successfully updated MarketData!"))

    def _update_fdr_data(self, exchange_name, df):
        # FDR data columns: Code, Name, Close, Volume, Amount, Marcap
        for index, row in df.iterrows():
            ticker = str(row.get('Code', ''))
            name = str(row.get('Name', ''))
            if not ticker: continue
            
            # Handle NaN values
            def _clean_val(v):
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    return 0
                return v

            close_price = _clean_val(row.get('Close', 0))
            volume = _clean_val(row.get('Volume', 0))
            amount = _clean_val(row.get('Amount', 0))
            marcap = _clean_val(row.get('Marcap', 0))
            
            MarketData.objects.update_or_create(
                exchange=exchange_name,
                ticker=ticker,
                defaults={
                    'name': name,
                    'close_price': float(close_price),
                    'volume': float(volume),
                    'amount': float(amount),
                    'market_cap': int(marcap) if marcap else None,
                }
            )

    def _update_upbit_data(self):
        # 1. Get Korean Names
        market_all_url = 'https://api.upbit.com/v1/market/all'
        market_all_data = requests.get(market_all_url).json()
        name_dict = {item['market']: item['korean_name'] for item in market_all_data if item['market'].startswith('KRW-')}
        
        # 2. Get Tickers and 24h Data
        tickers = list(name_dict.keys())
        chunk_size = 100
        
        for i in range(0, len(tickers), chunk_size):
            chunk = tickers[i:i+chunk_size]
            markets = ','.join(chunk)
            url = f'https://api.upbit.com/v1/ticker?markets={markets}'
            resp = requests.get(url).json()
            
            for item in resp:
                ticker = item['market']
                name = name_dict.get(ticker, ticker)
                close_price = float(item.get('trade_price', 0))
                volume = float(item.get('acc_trade_volume_24h', 0))
                amount = float(item.get('acc_trade_price_24h', 0))
                
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

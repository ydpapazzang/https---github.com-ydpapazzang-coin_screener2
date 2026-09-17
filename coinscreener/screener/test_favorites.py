import json
from django.test import TestCase, Client
from django.urls import reverse
from .models import Favorite, MarketData, Strategy, Condition, OHLCVCache


class FavoriteTests(TestCase):
    def setUp(self):
        self.client = Client()
        # MarketData sample
        MarketData.objects.create(
            exchange='upbit',
            ticker='KRW-BTC',
            name='비트코인',
            close_price=90000000,
            amount=50000000000,
        )
        MarketData.objects.create(
            exchange='upbit',
            ticker='KRW-ETH',
            name='이더리움',
            close_price=4000000,
            amount=30000000000,
        )

    def test_favorite_toggle(self):
        url = reverse('favorite_toggle')
        
        # 1. Toggle ON
        res = self.client.post(
            url,
            data=json.dumps({'ticker': 'KRW-BTC', 'name': '비트코인', 'exchange': 'upbit'}),
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['ok'])
        self.assertTrue(data['favorited'])
        self.assertEqual(Favorite.objects.filter(ticker='KRW-BTC').count(), 1)

        # 2. Toggle OFF
        res = self.client.post(
            url,
            data=json.dumps({'ticker': 'KRW-BTC', 'name': '비트코인', 'exchange': 'upbit'}),
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['ok'])
        self.assertFalse(data['favorited'])
        self.assertEqual(Favorite.objects.filter(ticker='KRW-BTC').count(), 0)

    def test_favorite_list_view(self):
        # Add a favorite first
        self.client.post(
            reverse('favorite_toggle'),
            data=json.dumps({'ticker': 'KRW-BTC', 'name': '비트코인', 'exchange': 'upbit'}),
            content_type='application/json',
        )
        res = self.client.get(reverse('favorite_list'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'KRW-BTC')
        self.assertContains(res, '비트코인')

    def test_favorite_add_modal(self):
        res = self.client.post(
            reverse('favorite_add'),
            data=json.dumps({
                'ticker': 'KRW-ETH',
                'name': '이더리움',
                'exchange': 'upbit',
                'memo': '눌림목 매수',
                'target_price': 4500000,
            }),
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['ok'])
        
        fav = Favorite.objects.filter(ticker='KRW-ETH').first()
        self.assertIsNotNone(fav)
        self.assertEqual(fav.memo, '눌림목 매수')
        self.assertEqual(fav.target_price, 4500000.0)

    def test_favorite_update_and_delete(self):
        # Add favorite
        self.client.post(
            reverse('favorite_add'),
            data=json.dumps({'ticker': 'KRW-BTC', 'name': '비트코인', 'exchange': 'upbit'}),
            content_type='application/json',
        )
        fav = Favorite.objects.filter(ticker='KRW-BTC').first()

        # Update memo & target_price
        update_url = reverse('favorite_update', args=[fav.id])
        res = self.client.post(
            update_url,
            data=json.dumps({'memo': '수정된 메모', 'target_price': 100000000}),
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 200)
        fav.refresh_from_db()
        self.assertEqual(fav.memo, '수정된 메모')
        self.assertEqual(fav.target_price, 100000000.0)

        # Delete favorite
        delete_url = reverse('favorite_delete', args=[fav.id])
        res = self.client.post(delete_url)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Favorite.objects.filter(id=fav.id).exists())

    def test_favorite_search_tickers(self):
        res = self.client.get(reverse('favorite_search_tickers') + '?q=비트&exchange=upbit')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['ok'])
        self.assertEqual(len(data['tickers']), 1)
        self.assertEqual(data['tickers'][0]['ticker'], 'KRW-BTC')

    def test_search_results_includes_favorites(self):
        # Favorite KRW-BTC
        self.client.post(
            reverse('favorite_toggle'),
            data=json.dumps({'ticker': 'KRW-BTC', 'name': '비트코인', 'exchange': 'upbit'}),
            content_type='application/json',
        )
        strategy = Strategy.objects.create(name='Fav Test Strategy')
        Condition.objects.create(
            strategy=strategy, timeframe='day', left_indicator='CLOSE', operator='gt', right_indicator='VAL', right_param=10
        )
        # Seed cache result
        OHLCVCache.objects.create(
            ticker=f"strategy_results_{strategy.id}_upbit_0",
            timeframe="RESULT",
            data={
                'results': [{
                    'symbol': 'KRW-BTC',
                    'name': '비트코인',
                    'price': 90000000,
                    'change_rate': 2.5,
                    'volume': 1000,
                    'amount_display': '1000억',
                    'volume_display': '1000',
                }],
                'rate_limit_warning': False,
                'elapsed_time': 0.1,
            }
        )
        res = self.client.get(reverse('coin_search_results', args=[strategy.id]))
        self.assertEqual(res.status_code, 200)
        self.assertIn('KRW-BTC', res.context['favorite_tickers'])

    def test_bithumb_removed_from_ui(self):
        # 1. Favorite list does not include Bithumb tab or option
        res = self.client.get(reverse('favorite_list'))
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, 'exchange=bithumb')
        self.assertNotContains(res, '빗썸')

        # 2. Strategy list does not include Bithumb sheet option or alert select
        res = self.client.get(reverse('strategy_list'))
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, 'selectEx(\'bithumb\'')
        self.assertNotContains(res, '<option value="bithumb">')

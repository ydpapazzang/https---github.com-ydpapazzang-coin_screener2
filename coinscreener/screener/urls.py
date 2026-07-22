from django.urls import path
from . import views

urlpatterns = [
    path('', views.strategy_list, name='strategy_list'),
    path('create/', views.strategy_create, name='strategy_create'),
    path('delete/', views.strategy_delete, name='strategy_delete'),

    path('strategy/<int:strategy_id>/',               views.strategy_detail,      name='strategy_detail'),
    path('trading/',                                  views.strategy_trading,     name='strategy_trading_root'),
    path('strategy/<int:strategy_id>/trading/',       views.strategy_trading,     name='strategy_trading'),
    path('strategy/<int:strategy_id>/save-risk/',     views.save_risk_settings,   name='save_risk_settings'),
    path('strategy/<int:strategy_id>/rename/',        views.strategy_rename,      name='strategy_rename'),
    path('strategy/<int:strategy_id>/scan-count/',    views.strategy_scan_count,  name='strategy_scan_count'),
    path('strategy/<int:strategy_id>/search/',        views.coin_search,          name='coin_search'),
    path('strategy/<int:strategy_id>/search-stream/', views.coin_search_stream,   name='coin_search_stream'),
    path('strategy/<int:strategy_id>/results/',       views.coin_search_results,  name='coin_search_results'),

    path('strategy/<int:strategy_id>/condition/add/',                       views.condition_add,    name='condition_add'),
    path('strategy/<int:strategy_id>/condition/<int:condition_id>/delete/', views.condition_delete, name='condition_delete'),

    # 알림 API
    path('strategy/<int:strategy_id>/alert/',          views.alert_get,      name='alert_get'),
    path('strategy/<int:strategy_id>/alert/save/',     views.alert_save,     name='alert_save'),
    path('strategy/<int:strategy_id>/alert/send-now/', views.alert_send_now, name='alert_send_now'),

    # 종목 딥링크 리다이렉터 (텔레그램 → 앱/모바일웹)
    path('open/', views.open_market, name='open_market'),

    # 백테스팅 API
    path('backtest/coins/',                          views.backtest_coins, name='backtest_coins'),
    path('strategy/<int:strategy_id>/backtest/run/', views.backtest_run,   name='backtest_run'),
    path('cron/scan',                                views.cron_scan,      name='cron_scan_no_slash'),
    path('cron/scan/',                               views.cron_scan,      name='cron_scan'),
    path('cron/scan-debug',                          views.cron_scan,      name='cron_scan_debug_no_slash'),
    path('cron/scan-debug/',                         views.cron_scan,      name='cron_scan_debug'),
    path('cron/prefetch/',                           views.cron_prefetch,  name='cron_prefetch'),
    path('cron/migrate/',                            views.trigger_migrate,name='trigger_migrate'),
    path('cron/debug/',                              views.trigger_debug,  name='trigger_debug'),
]
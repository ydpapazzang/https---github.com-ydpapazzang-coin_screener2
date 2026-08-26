from django.urls import path
from . import views

urlpatterns = [
    path('healthz/', views.healthz, name='healthz'),
    path('', views.strategy_list, name='strategy_list'),
    path('create/', views.strategy_create, name='strategy_create'),
    path('delete/', views.strategy_delete, name='strategy_delete'),
    path('strategy/<int:strategy_id>/clone/', views.strategy_clone, name='strategy_clone'),

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
    path('cron/scan/',                               views.cron_scan,      name='cron_scan'),
    path('cron/scan-debug/',                         views.cron_scan,      name='cron_scan_debug'),
    path('cron/prefetch/',                           views.cron_prefetch,  name='cron_prefetch'),
    
    # 단타·스윙 추천 및 통계 탭
    path('danta/', views.danta_list, name='danta_list'),
    path('swing/', views.swing_list, name='swing_list'),
    path('stats/', views.stats_list, name='stats_list'),

    # 정보/약관/가이드 페이지 (애드센스 승인·신뢰도용)
    path('more/',    views.more_menu, name='more_menu'),
    path('about/',   views.about,     name='about'),
    path('privacy/', views.privacy,   name='privacy'),
    path('terms/',   views.terms,     name='terms'),
    path('contact/', views.contact,   name='contact'),
    path('guide/',            views.guide_list,   name='guide_list'),
    path('guide/<slug:slug>/', views.guide_detail, name='guide_detail'),

    # 백오피스 (PC 관리자용, 슈퍼유저 로그인 필요)
    path('manage/',        views.manage_dashboard, name='manage_dashboard'),
    path('manage/alerts/', views.manage_alerts,    name='manage_alerts'),
    path('manage/danta/',  views.manage_danta,     name='manage_danta'),
    path('manage/visits/', views.manage_visits,    name='manage_visits'),
]


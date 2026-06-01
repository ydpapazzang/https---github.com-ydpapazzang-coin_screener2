from django.urls import path
from . import views

urlpatterns = [
    path('', views.strategy_list, name='strategy_list'),
    path('create/', views.strategy_create, name='strategy_create'),
    path('delete/', views.strategy_delete, name='strategy_delete'),

    path('strategy/<int:strategy_id>/',               views.strategy_detail,      name='strategy_detail'),
    path('strategy/<int:strategy_id>/search/',        views.coin_search,          name='coin_search'),
    path('strategy/<int:strategy_id>/search-stream/', views.coin_search_stream,   name='coin_search_stream'),
    path('strategy/<int:strategy_id>/results/',       views.coin_search_results,  name='coin_search_results'),

    path('strategy/<int:strategy_id>/condition/add/',                       views.condition_add,    name='condition_add'),
    path('strategy/<int:strategy_id>/condition/<int:condition_id>/delete/', views.condition_delete, name='condition_delete'),

    # 알림 API
    path('strategy/<int:strategy_id>/alert/',          views.alert_get,      name='alert_get'),
    path('strategy/<int:strategy_id>/alert/save/',     views.alert_save,     name='alert_save'),
    path('strategy/<int:strategy_id>/alert/send-now/', views.alert_send_now, name='alert_send_now'),

    # 백테스팅 API
    path('backtest/coins/',                          views.backtest_coins, name='backtest_coins'),
    path('strategy/<int:strategy_id>/backtest/run/', views.backtest_run,   name='backtest_run'),
    path('db-debug/',                                views.db_debug,       name='db_debug'),
]
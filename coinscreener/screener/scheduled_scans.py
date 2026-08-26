"""Gunicorn과 분리해 실행하는 예약 전략 스캔 서비스."""
import traceback

from django.db.models import Q
from django.utils import timezone

from . import telegram as tg
from .freshness import scan_freshness
from .models import AlertSetting
from .views.scan_views import _get_tickers
from .views.strategy_views import process_scan_and_alert


def _emit(output, message):
    if output:
        output(message)


def run_scheduled_scans(force=False, now_kst=None, output=print):
    """현재 KST 30분 슬롯의 예약 전략을 별도 프로세스에서 순차 실행한다."""
    now_kst = now_kst or timezone.localtime(timezone.now())
    slot_minute = 0 if now_kst.minute < 30 else 30
    slot_start = now_kst.replace(
        minute=slot_minute, second=0, microsecond=0,
    )
    settings = AlertSetting.objects.filter(enabled=True).select_related('strategy')
    if not force:
        settings = settings.filter(
            alert_hour=slot_start.hour,
            alert_min=slot_start.minute,
        )

    summary = {
        'ok': True,
        'time': now_kst.strftime('%Y-%m-%d %H:%M:%S KST'),
        'processed': 0,
        'sent_alerts': 0,
        'warnings': [],
        'details': [],
    }
    _emit(output, f"[SCHEDULED_SCAN] slot={slot_start:%H:%M} count={settings.count()}")

    for setting in settings:
        if not force:
            claimed = AlertSetting.objects.filter(pk=setting.pk).filter(
                Q(last_run_at__isnull=True) | Q(last_run_at__lt=slot_start)
            ).update(last_run_at=now_kst)
            if not claimed:
                _emit(output, f"[SCHEDULED_SCAN] already claimed setting={setting.pk}")
                continue

        strategy = setting.strategy
        try:
            conditions = list(strategy.conditions.all())
            if not conditions:
                raise RuntimeError('전략 조건이 없습니다.')

            tickers = _get_tickers(setting.exchange, setting.vol_limit)
            if not tickers:
                raise RuntimeError('스캔 대상 종목이 없습니다.')

            freshness = scan_freshness(tickers, conditions)
            if not freshness['ok']:
                raise RuntimeError(
                    '시세 캐시 지연: '
                    f"신선 {freshness['fresh']}/{freshness['expected']} "
                    f"({freshness['fresh_ratio']}%)"
                )

            _emit(
                output,
                f"[SCHEDULED_SCAN] strategy={strategy.id}:{strategy.name} "
                f"exchange={setting.exchange} tickers={len(tickers)} "
                f"fresh={freshness['fresh_ratio']}%",
            )
            results, telegram_results = process_scan_and_alert(
                strategy, tickers, conditions, exchange=setting.exchange,
            )
            telegram_result = {'ok': True, 'skipped': True}
            if tg.is_configured():
                if results and not telegram_results:
                    telegram_result = {
                        'ok': True,
                        'skipped': True,
                        'reason': 'duplicate_suppressed',
                    }
                else:
                    telegram_result = tg.send_alert(
                        strategy.name,
                        telegram_results,
                        strategy_id=strategy.id,
                        exchange=setting.exchange,
                    )

            summary['processed'] += 1
            if telegram_result.get('ok') and not telegram_result.get('skipped'):
                summary['sent_alerts'] += 1
            summary['details'].append({
                'strategy': strategy.name,
                'matched_count': len(results),
                'sent_count': len(telegram_results),
                'fresh_ratio': freshness['fresh_ratio'],
                'telegram_result': telegram_result,
            })
        except Exception as exc:
            warning = f"{strategy.name}({setting.exchange}) 실패: {exc}"
            summary['warnings'].append(warning)
            summary['ok'] = False
            _emit(output, f"[SCHEDULED_SCAN_ERROR] {warning}")
            _emit(output, traceback.format_exc())

    if summary['warnings'] and tg.is_configured():
        tg.send_message(
            '⚠️ <b>예약 전략 스캔 경고</b>\n'
            + '\n'.join(summary['warnings'][:10])
        )
    return summary


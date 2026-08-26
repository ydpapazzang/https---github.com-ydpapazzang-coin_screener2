from django.core.management.base import BaseCommand

from coinscreener.screener import telegram as tg
from coinscreener.screener.system_health import (
    collect_health,
    load_monitor_state,
    save_monitor_state,
)


class Command(BaseCommand):
    help = '웹·DB·시세·메모리·디스크 상태를 확인하고 상태 변경 시 알립니다.'

    def handle(self, *args, **options):
        snapshot = collect_health(include_web=True)
        previous = load_monitor_state()
        previous_status = previous.get('status')
        changed = (
            (previous_status is None and snapshot['status'] != 'ok')
            or (previous_status is not None and previous_status != snapshot['status'])
        )

        if changed and tg.is_configured():
            if snapshot['status'] == 'ok':
                message = '✅ <b>Coin Screener 정상 복구</b>'
            else:
                items = snapshot['problems'] + snapshot['warnings']
                message = (
                    f"⚠️ <b>Coin Screener {snapshot['status'].upper()}</b>\n"
                    + '\n'.join(f'• {item}' for item in items)
                )
            result = tg.send_message(message)
            self.stdout.write(f'[HEALTH_NOTIFY] {result}')

        save_monitor_state(snapshot)
        self.stdout.write(
            '[HEALTH] '
            f"status={snapshot['status']} db={snapshot['database_ok']} "
            f"web={snapshot['web_ok']} cache_age={snapshot['cache_age_minutes']}m "
            f"memory_available={snapshot['memory'].get('available_pct')}% "
            f"disk_free={snapshot['disk_free_pct']}%"
        )


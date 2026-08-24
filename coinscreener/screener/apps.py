import logging

from django.apps import AppConfig
from django.db import OperationalError
from django.db.backends.signals import connection_created


logger = logging.getLogger(__name__)


def configure_sqlite_connection(sender, connection, **kwargs):
    """운영 SQLite 연결에 동시성 친화적인 PRAGMA를 적용한다."""
    if connection.vendor != 'sqlite':
        return

    try:
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA busy_timeout=30000')
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute('PRAGMA synchronous=NORMAL')
    except OperationalError as exc:
        # WAL은 DB 파일에 영구 적용되므로 일시적인 경합으로 이번 연결에서
        # 설정하지 못해도 기존 모드로 기동하고 다음 연결에서 재시도한다.
        logger.warning('SQLite connection PRAGMA setup deferred: %s', exc)


class ScreenerConfig(AppConfig):
    name = 'coinscreener.screener'

    def ready(self):
        connection_created.connect(
            configure_sqlite_connection,
            dispatch_uid='coinscreener.configure_sqlite_connection',
        )

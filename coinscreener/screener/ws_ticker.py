"""Upbit WebSocket 실시간 현재가 수신 모듈.

서버 시작 시 Upbit WebSocket(wss://api.upbit.com/websocket/v1)에 상시 연결하여
전체 KRW 마켓 코인의 현재가·등락률·24시간 거래대금을 실시간으로 수신하고
메모리 딕셔너리에 유지한다.

scan_views._enrich_upbit_ticker_snapshot()에서 REST API 대신 이 캐시를 우선 사용한다.
연결 끊김 시 지수 백오프로 자동 재연결한다.
"""
import json
import logging
import os
import threading
import time
import uuid

logger = logging.getLogger(__name__)

# 수신된 스냅샷을 저장하는 공유 딕셔너리.
# 키: 'KRW-BTC' 형식 마켓 코드
# 값: {'trade_price': float, 'signed_change_rate': float, 'acc_trade_price_24h': float}
_WS_PRICE_CACHE: dict = {}
_WS_CACHE_LOCK = threading.Lock()

# 마지막으로 데이터를 수신한 시각 (time.monotonic())
_WS_LAST_RECV: float = 0.0

# WebSocket 스레드가 이미 시작됐는지 여부
_WS_STARTED = False
_WS_START_LOCK = threading.Lock()

# 연결 상태 공유
_WS_CONNECTED = False


def get_ws_snapshot() -> dict:
    """현재 메모리 캐시의 스냅샷을 복사해 반환한다. 스레드 안전."""
    with _WS_CACHE_LOCK:
        return dict(_WS_PRICE_CACHE)


def get_ws_snapshot_age() -> float:
    """마지막 데이터 수신으로부터 경과한 시간(초). 아직 미수신이면 inf 반환."""
    last = _WS_LAST_RECV
    if last == 0.0:
        return float('inf')
    return time.monotonic() - last


def is_ws_connected() -> bool:
    """WebSocket이 현재 연결 중인지 여부."""
    return _WS_CONNECTED


def _fetch_krw_markets() -> list[str]:
    """Upbit REST API로 KRW 마켓 목록 조회."""
    import requests as _req
    try:
        resp = _req.get('https://api.upbit.com/v1/market/all', timeout=5)
        markets = resp.json()
        return [
            m['market'] for m in markets
            if isinstance(m, dict) and m.get('market', '').startswith('KRW-')
        ]
    except Exception as exc:
        logger.warning('[WS_TICKER] KRW 마켓 목록 조회 실패: %s', exc)
        return []


def _ws_worker():
    """백그라운드 WebSocket 수신 스레드 메인 루프."""
    global _WS_CONNECTED, _WS_LAST_RECV

    try:
        import websocket  # websocket-client 패키지
    except ImportError:
        logger.error(
            '[WS_TICKER] websocket-client 패키지가 없습니다. '
            'pip install websocket-client 후 재시작하세요.'
        )
        return

    UPBIT_WS_URL = 'wss://api.upbit.com/websocket/v1'
    backoff = 1  # 재연결 대기시간(초), 최대 60초

    while True:
        markets = _fetch_krw_markets()
        if not markets:
            logger.warning('[WS_TICKER] 마켓 목록을 가져오지 못했습니다. %d초 후 재시도.', backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue

        # Upbit WebSocket 구독 메시지 포맷
        ticket = str(uuid.uuid4())
        subscribe_msg = json.dumps([
            {'ticket': ticket},
            {
                'type': 'ticker',
                'codes': markets,
                'isOnlyRealtime': False,  # 초기 스냅샷 포함 (서버시작 직후에도 데이터 즉시 수신)
            },
            {'format': 'SIMPLE'},  # 데이터 크기 최소화
        ])

        ws = websocket.WebSocket()
        try:
            logger.info('[WS_TICKER] Upbit WebSocket 연결 중... (%d개 마켓)', len(markets))
            ws.connect(UPBIT_WS_URL, timeout=10)
            ws.send(subscribe_msg)
            _WS_CONNECTED = True
            backoff = 1  # 성공 시 backoff 리셋
            logger.info('[WS_TICKER] 연결 성공. 실시간 시세 수신 시작.')

            # Upbit은 120초마다 ping 필요 (60초 주기로 여유있게)
            last_ping = time.monotonic()
            ws.settimeout(65)  # 65초 내에 메시지 없으면 타임아웃 처리

            while True:
                try:
                    raw = ws.recv()
                    if not raw:
                        continue

                    # SIMPLE 포맷 바이너리/텍스트 모두 처리
                    if isinstance(raw, bytes):
                        raw = raw.decode('utf-8')

                    data = json.loads(raw)
                    market = data.get('cd')  # SIMPLE 포맷: 'cd' = market code
                    if not market:
                        continue

                    price = data.get('tp')          # trade_price
                    change_rate = data.get('scr')   # signed_change_rate
                    acc_amount = data.get('atp24h') # acc_trade_price_24h

                    if price is not None:
                        with _WS_CACHE_LOCK:
                            _WS_PRICE_CACHE[market] = {
                                'trade_price': float(price),
                                'signed_change_rate': float(change_rate) if change_rate is not None else 0.0,
                                'acc_trade_price_24h': float(acc_amount) if acc_amount is not None else 0.0,
                            }
                        _WS_LAST_RECV = time.monotonic()

                    # 60초마다 ping 전송
                    now = time.monotonic()
                    if now - last_ping > 60:
                        ws.ping()
                        last_ping = now

                except websocket.WebSocketTimeoutException:
                    # 65초 동안 메시지 없으면 연결 재확인을 위해 ping
                    try:
                        ws.ping()
                        last_ping = time.monotonic()
                    except Exception:
                        break
                except (websocket.WebSocketConnectionClosedException,
                        websocket.WebSocketProtocolException):
                    logger.warning('[WS_TICKER] 연결 종료. 재연결 예정.')
                    break
                except Exception as exc:
                    logger.warning('[WS_TICKER] 수신 오류: %s', exc)
                    break

        except Exception as exc:
            logger.warning('[WS_TICKER] 연결 실패: %s. %d초 후 재시도.', exc, backoff)
        finally:
            _WS_CONNECTED = False
            try:
                ws.close()
            except Exception:
                pass

        time.sleep(backoff)
        backoff = min(backoff * 2, 60)


def start_ws_ticker():
    """WebSocket 수신 스레드를 시작한다. 이미 시작됐으면 무시.

    Django apps.py ready()에서 호출되도록 설계됐다.
    runserver의 auto-reload 이중 프로세스 문제를 방어하기 위해
    RUN_MAIN 환경변수 체크를 포함한다.
    """
    global _WS_STARTED

    # runserver --reload 시 reloader 부모 프로세스에서는 실행하지 않는다.
    # 실제 워커 프로세스(RUN_MAIN=true)에서만 시작.
    if os.environ.get('RUN_MAIN') == 'true' or not _is_dev_server():
        pass  # gunicorn / 실제 워커 → 진행
    else:
        logger.debug('[WS_TICKER] runserver reloader 프로세스 감지. WS 스레드 생략.')
        return

    with _WS_START_LOCK:
        if _WS_STARTED:
            return
        _WS_STARTED = True

    t = threading.Thread(
        target=_ws_worker,
        name='upbit-ws-ticker',
        daemon=True,  # 메인 프로세스 종료 시 자동 종료
    )
    t.start()
    logger.info('[WS_TICKER] 실시간 현재가 WebSocket 스레드 시작됨.')


def _is_dev_server() -> bool:
    """Django runserver로 실행 중인지 감지."""
    import sys
    return 'runserver' in sys.argv

"""텔레그램 봇 유틸리티"""
import os
import html as _html
import requests
from urllib.parse import quote


def _get_token() -> str:
    """매 호출마다 환경변수를 새로 읽음 (Vercel 환경변수 반영 보장)"""
    return os.environ.get('TELEGRAM_BOT_TOKEN', '')


def _get_chat_id() -> str:
    return os.environ.get('TELEGRAM_CHAT_ID', '')


def is_configured() -> bool:
    """환경변수 설정 여부를 매번 동적으로 확인"""
    return bool(_get_token() and _get_chat_id())


def send_message(text: str) -> dict:
    """텔레그램 메시지 발송. 성공 시 {'ok': True}, 실패 시 {'ok': False, 'error': ...}"""
    token   = _get_token()
    chat_id = _get_chat_id()

    if not token or not chat_id:
        return {'ok': False, 'error': '환경변수 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정'}

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        r = requests.post(url, json={
            'chat_id':    chat_id,
            'text':       text,
            'parse_mode': 'HTML',
        }, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get('ok'):
            return {'ok': True}
        return {'ok': False, 'error': data.get('description', '알 수 없는 오류')}
    except requests.exceptions.Timeout:
        return {'ok': False, 'error': '텔레그램 API 응답 시간 초과 (10초)'}
    except requests.exceptions.ConnectionError:
        return {'ok': False, 'error': '텔레그램 API 연결 실패 — 네트워크를 확인하세요'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def shorten_url(url: str) -> str:
    """TinyURL API를 사용해 긴 웹사이트 주소를 짧은 단축 URL로 변환 (실패 시 원본 URL로 안전 폴백)"""
    try:
        r = requests.get(f"https://tinyurl.com/api-create.php?url={quote(url, safe='')}", timeout=5)
        if r.status_code == 200:
            shorturl = r.text.strip()
            if shorturl.startswith("http"):
                return shorturl
    except Exception:
        pass
    return url


EXCHANGE_LABEL = {'upbit': '업비트', 'bithumb': '빗썸', 'kospi': '코스피'}


def market_link(exchange: str, symbol: str) -> str:
    """거래소별 종목 딥링크(유니버설 링크) 생성.
    모바일에서 해당 앱이 설치돼 있으면 앱으로, 없으면 모바일 웹으로 연결된다.
    - 업비트 → upbit.com (업비트 앱)
    - 빗썸   → bithumb.com (빗썸 앱)
    - 코스피 → m.stock.naver.com (네이버 증권/주식)
    """
    coin = symbol.replace('KRW-', '').strip()
    if exchange == 'upbit':
        market = symbol if symbol.startswith('KRW-') else f'KRW-{coin}'
        # 앱이 App Link로 등록하는 정식 코인 상세 경로 형식
        return f'https://www.upbit.com/exchange/CRIX.UPBIT.{market}'
    if exchange == 'bithumb':
        return f'https://www.bithumb.com/react/trade/order/{coin}-KRW'
    if exchange == 'kospi':
        # 코스피는 symbol이 종목코드(예: 005930)
        return f'https://m.stock.naver.com/domestic/stock/{symbol}/total'
    return ''


def _site_url() -> str:
    """알림 링크에 사용할 사이트 절대 URL (SITE_URL 우선, 없으면 VERCEL_URL)."""
    site_url = os.environ.get('SITE_URL', '').strip()
    source = 'SITE_URL'
    if not site_url:
        site_url = os.environ.get('VERCEL_URL', '').strip()
        source = 'VERCEL_URL'
    if site_url and not site_url.startswith('http'):
        site_url = 'https://' + site_url
    site_url = site_url.rstrip('/')
    print(f"[TELEGRAM] Link site_url: '{site_url}' (retrieved from {source})")
    return site_url


def _symbol_link(exchange: str, symbol: str, site_url: str) -> str:
    """종목 링크. 사이트 URL이 있으면 앱 딥링크 리다이렉터(/open/)로, 없으면 거래소 웹으로."""
    if site_url:
        return f'{site_url}/open/?ex={quote(exchange)}&sym={quote(symbol)}'
    return market_link(exchange, symbol)


def send_alert(strategy_name: str, results: list, strategy_id: int = None, exchange: str = 'upbit') -> dict:
    """스크리닝 결과 알림 발송"""
    ex_label = EXCHANGE_LABEL.get(exchange, exchange)
    site_url = _site_url()

    # 동적 텍스트 HTML 이스케이프 (인젝션 방어)
    safe_name = _html.escape(strategy_name)

    if not results:
        text = f"📊 <b>{safe_name}</b>  <i>[{ex_label}]</i>\n조건에 맞는 코인이 없습니다."
    else:
        lines = [f"📊 <b>{safe_name}</b>  <i>[{ex_label}]</i> — {len(results)}개 매칭\n"]
        for r in results[:20]:  # 최대 20개
            vol         = r.get('volume_display', '')
            status_icon = '🆕' if r.get('status') == 'new' else '🔁'
            price_str   = f"{r['price']:,.0f}" if r.get('price') else '-'
            raw_name    = r.get('name', '')
            name_str    = f"[{_html.escape(raw_name)}] " if raw_name and raw_name != r['symbol'] else ""
            link        = _symbol_link(exchange, r['symbol'], site_url)
            safe_symbol = _html.escape(r['symbol'])
            symbol_html = f'<a href="{link}">{safe_symbol}</a>' if link else f"<b>{safe_symbol}</b>"
            lines.append(f"{status_icon} {name_str}{symbol_html}  {price_str}원  거래대금 {vol}")
        if len(results) > 20:
            lines.append(f"... 외 {len(results) - 20}개")
        text = "\n".join(lines)

    if strategy_id and site_url:
        link  = f'{site_url}/strategy/{strategy_id}/'
        text += f'\n\n<a href="{link}">🔗 웹에서 보기</a>'

    return send_message(text)

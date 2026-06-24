"""텔레그램 봇 유틸리티"""
import os
import requests


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
        r = requests.get(f"https://tinyurl.com/api-create.php?url={url}", timeout=5)
        if r.status_code == 200:
            shorturl = r.text.strip()
            if shorturl.startswith("http"):
                return shorturl
    except Exception:
        pass
    return url


def send_alert(strategy_name: str, results: list, strategy_id: int = None) -> dict:
    """스크리닝 결과 알림 발송"""
    if not results:
        text = f"📊 <b>{strategy_name}</b>\n조건에 맞는 코인이 없습니다."
    else:
        lines = [f"📊 <b>{strategy_name}</b> — {len(results)}개 매칭\n"]
        for r in results[:20]:  # 최대 20개
            vol         = r.get('volume_display', '')
            status_icon = '🆕' if r.get('status') == 'new' else '🔁'
            price_str   = f"{r['price']:,.0f}" if r.get('price') else '-'
            lines.append(f"{status_icon} <b>{r['symbol']}</b>  {price_str}원  거래대금 {vol}")
        if len(results) > 20:
            lines.append(f"... 외 {len(results) - 20}개")
        text = "\n".join(lines)

    # Vercel 환경변수 중 SITE_URL을 최우선으로 사용 (미설정 시 VERCEL_URL로 안전 폴백)
    site_url = os.environ.get('SITE_URL', '').strip()
    source = 'SITE_URL'
    
    if not site_url:
        site_url = os.environ.get('VERCEL_URL', '').strip()
        source = 'VERCEL_URL'

    print(f"[TELEGRAM] Link site_url: '{site_url}' (retrieved from {source})")

    if strategy_id and site_url:
        if not site_url.startswith('http'):
            site_url = 'https://' + site_url
        site_url = site_url.rstrip('/')
        link  = f'{site_url}/strategy/{strategy_id}/'
        text += f'\n\n<a href="{link}">🔗 웹에서 보기</a>'

    return send_message(text)

"""방문 기록 미들웨어.

e2-micro + SQLite 환경이라 쓰기 부하를 최소화한다:
 - GET, HTML 응답, 실제 페이지뷰만 기록
 - 정적파일 / admin / manage(백오피스 자기 자신) / cron / API·스트림 경로는 제외
 - 어떤 예외도 요청 처리를 막지 않도록 전부 삼킨다
"""

# 경로 접두사로 스킵 (정적/관리/크론/딥링크 등)
SKIP_PREFIXES = (
    '/static', '/admin', '/manage', '/cron', '/open', '/favicon',
)

# 경로에 포함되면 스킵 (검색 스트림·부분 API 등 페이지뷰가 아닌 것)
SKIP_CONTAINS = (
    '/search-stream', '/search/', '/results', '/scan-count',
    '/alert', '/backtest', '/condition', '/save-risk', '/rename',
)


class VisitLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            self._log(request, response)
        except Exception:
            # 로깅 실패가 사용자 요청을 절대 막지 않도록
            pass
        return response

    def _log(self, request, response):
        if request.method != 'GET':
            return

        path = request.path
        for p in SKIP_PREFIXES:
            if path.startswith(p):
                return
        for c in SKIP_CONTAINS:
            if c in path:
                return

        ctype = response.get('Content-Type', '') or ''
        if 'text/html' not in ctype:
            return

        # 지연 import: 앱 로딩 순서 이슈 방지
        from .models import VisitLog

        # Nginx 리버스 프록시 뒤라 실제 클라이언트 IP는 X-Forwarded-For 첫 값
        xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
        ip = xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR')

        VisitLog.objects.create(
            path=path[:300],
            method=request.method,
            ip=ip or None,
            user_agent=(request.META.get('HTTP_USER_AGENT', '') or '')[:300],
            referer=(request.META.get('HTTP_REFERER', '') or '')[:300],
            status_code=response.status_code,
        )

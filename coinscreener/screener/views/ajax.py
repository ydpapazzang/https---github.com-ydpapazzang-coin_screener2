from functools import wraps

from django.http import JsonResponse


def ajax_redirect(view):
    """기존 POST-redirect 흐름을 AJAX 요청에서는 JSON으로 바꾼다.

    비-AJAX 요청은 기존 동작을 그대로 유지하므로, 자바스크립트가 꺼진 환경과
    외부 링크·테스트의 호환성이 보장된다.
    """
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        if (
            request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            and 300 <= response.status_code < 400
            and response.get('Location')
        ):
            return JsonResponse({
                'ok': True,
                'redirect_url': response['Location'],
            })
        return response
    return wrapped


from django.conf import settings


def site(request):
    """모든 템플릿에서 광고/디버그 상태에 접근할 수 있게 주입."""
    return {
        'ADSENSE_CLIENT': getattr(settings, 'ADSENSE_CLIENT', ''),
        'IS_DEBUG': settings.DEBUG,
    }

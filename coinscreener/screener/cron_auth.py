import os
from secrets import compare_digest


def is_cron_request_authorized(request):
    """Authenticate cron requests without putting credentials in the URL."""
    expected_secret = os.environ.get("CRON_SECRET", "")
    authorization = request.headers.get("Authorization", "")
    scheme, separator, provided_secret = authorization.partition(" ")

    return (
        bool(expected_secret)
        and bool(separator)
        and scheme.lower() == "bearer"
        and bool(provided_secret)
        and compare_digest(provided_secret, expected_secret)
    )

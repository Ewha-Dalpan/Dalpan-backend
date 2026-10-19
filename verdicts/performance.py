"""Timing without case text, and shared search admission across worker processes."""
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import SearchThrottle

logger = logging.getLogger(__name__)
_request_id = ContextVar("verdict_timing_request_id", default=None)


@contextmanager
def timed(stage, *, request_id=None):
    token = _request_id.set(request_id) if request_id is not None else None
    started = time.monotonic()
    try:
        yield
    finally:
        logger.info("AI timing: request_id=%s stage=%s elapsed_ms=%.0f",
                    _request_id.get(), stage, (time.monotonic() - started) * 1000)
        if token is not None:
            _request_id.reset(token)


def admit_search(kind, timeout):
    # No transaction is held while sleeping or making HTTP calls. The conditional
    # UPDATE is atomic on PostgreSQL, including across separate worker services.
    interval = (settings.LINER_SCHOLAR_INTERVAL_SECONDS if kind == "SCHOLAR"
                else settings.LINER_WEB_INTERVAL_SECONDS)
    deadline = time.monotonic() + timeout
    SearchThrottle.objects.get_or_create(kind=kind, defaults={"next_at": timezone.now()})
    while True:
        now = timezone.now()
        if time.monotonic() >= deadline:
            from .ai_client import AIClientError
            raise AIClientError("\uac80\uc0c9 \ub300\uae30 \uc2dc\uac04\uc774 \ucd08\uacfc\ub418\uc5c8\uc2b5\ub2c8\ub2e4.")
        if SearchThrottle.objects.filter(kind=kind, next_at__lte=now).update(
            next_at=now + timedelta(seconds=interval),
        ):
            return
        next_at = SearchThrottle.objects.values_list("next_at", flat=True).get(kind=kind)
        time.sleep(min(max((next_at - now).total_seconds(), 0.01), 0.25,
                       max(0.001, deadline - time.monotonic())))


def defer_search(kind, seconds):
    until = timezone.now() + timedelta(seconds=seconds)
    SearchThrottle.objects.filter(kind=kind, next_at__lt=until).update(next_at=until)

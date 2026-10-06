# verdicts/services.py
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from cases.models import Case
from coins.services import restore_for_verdict

from .models import Verdict, VerdictReason, VerdictRequest

ACTIVE = ("PENDING", "RUNNING")


def _timeout():
    return timedelta(seconds=getattr(settings, "VERDICT_TIMEOUT_SECONDS", 600))


def complete_request(request_id, *, fault_ratio, judgment_text, reasons, **optional):
    """판결 성공 처리. 판결문·과실 비율·판단 근거가 모두 있을 때만 저장한다. 저장했으면 True."""
    reasons = [r.strip() for r in (reasons or []) if r and r.strip()]
    if not (judgment_text or "").strip() or not reasons or not (0 <= fault_ratio <= 100):
        return fail_request(request_id, "LLM 결과가 올바르지 않습니다.")

    with transaction.atomic():
        req = VerdictRequest.objects.select_for_update().get(pk=request_id)
        if req.status not in ACTIVE:  # 이미 시간 초과 등으로 닫힌 요청이면 결과를 버린다
            return False
        verdict = Verdict.objects.create(
            request=req, case_id=req.case_id, fault_ratio=fault_ratio,
            judgment_text=judgment_text, **optional,
        )
        VerdictReason.objects.bulk_create(
            [VerdictReason(verdict=verdict, content=c, sort_order=i) for i, c in enumerate(reasons)]
        )
        req.status = "DONE"
        req.finished_at = timezone.now()
        req.save(update_fields=["status", "finished_at"])
        Case.objects.filter(pk=req.case_id).update(status="JUDGED")
    return True


def fail_request(request_id, reason):
    """판결 실패 처리 + 코인 반환. 이미 닫힌 요청이면 아무것도 하지 않는다."""
    with transaction.atomic():
        req = VerdictRequest.objects.select_for_update().get(pk=request_id)
        if req.status not in ACTIVE:
            return False
        req.status = "FAILED"
        req.error_message = str(reason)[:1000]
        req.finished_at = timezone.now()
        req.save(update_fields=["status", "error_message", "finished_at"])
        restore_for_verdict(verdict_request=req)
        Case.objects.filter(pk=req.case_id).update(status="READY")  # 다시 요청할 수 있게
    return True


def expire_if_stale(req):
    """상태 조회(폴링) API에서 호출: 오래된 요청이면 그 자리에서 실패 처리."""
    if req.status in ACTIVE and req.created_at < timezone.now() - _timeout():
        fail_request(req.pk, "처리 시간 초과")


def fail_stale_requests() -> int:
    cutoff = timezone.now() - _timeout()
    ids = list(VerdictRequest.objects.filter(status__in=ACTIVE, created_at__lt=cutoff).values_list("pk", flat=True))
    return sum(fail_request(pk, "처리 시간 초과") for pk in ids)
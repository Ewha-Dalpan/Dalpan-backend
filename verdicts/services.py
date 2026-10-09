# verdicts/services.py
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models.functions import Coalesce
from django.utils import timezone

from cases.models import Case, CaseIssue, CaseSituation
from coins.services import restore_for_verdict

from .models import Verdict, VerdictReason, VerdictRequest

ACTIVE = ("PENDING", "RUNNING")


def _locked_request(request_id):
    # 접수/판결 요청과 동일하게 사건 → 요청 순서로 잠근다.
    case_id = VerdictRequest.objects.values_list('case_id', flat=True).get(pk=request_id)
    Case.objects.select_for_update().get(pk=case_id)
    return VerdictRequest.objects.select_for_update().get(pk=request_id)


def _timeout():
    return timedelta(seconds=getattr(settings, "VERDICT_TIMEOUT_SECONDS", 600))


def complete_request(request_id, *, fault_ratio, judgment_text, reasons, **optional):
    """판결 성공 처리. 판결문·과실 비율·판단 근거가 모두 있을 때만 저장한다. 저장했으면 True."""
    reasons = [r.strip() for r in (reasons or []) if r and r.strip()]
    if not (judgment_text or "").strip() or not reasons or not (0 <= fault_ratio <= 100):
        return fail_request(request_id, "LLM 결과가 올바르지 않습니다.")

    with transaction.atomic():
        req = _locked_request(request_id)
        if req.status not in ACTIVE or req.stage != "JUDGMENT":  # 이미 시간 초과 등으로 닫힌 요청이면 결과를 버린다
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
        Case.objects.filter(pk=req.case_id).update(status="JUDGED", updated_at=timezone.now())
    return True


def fail_request(request_id, reason):
    """판결 실패 처리 + 코인 반환. 이미 닫힌 요청이면 아무것도 하지 않는다."""
    with transaction.atomic():
        req = _locked_request(request_id)
        if req.status not in ACTIVE:
            return False
        req.status = "FAILED"
        req.error_message = str(reason)[:1000]
        req.finished_at = timezone.now()
        req.save(update_fields=["status", "error_message", "finished_at"])
        restore_for_verdict(verdict_request=req)
        if req.stage == 'ANALYSIS':
            CaseSituation.objects.filter(case_id=req.case_id).update(analysis_status='FAILED')
            Case.objects.filter(pk=req.case_id).update(status='WRITING', updated_at=timezone.now())
        else:
            Case.objects.filter(pk=req.case_id).update(status='READY', updated_at=timezone.now())
    return True


def expire_if_stale(req):
    """상태 조회(폴링) API에서 호출: 오래된 요청이면 그 자리에서 실패 처리."""
    if req.status in ACTIVE and (req.started_at or req.created_at) < timezone.now() - _timeout():
        fail_request(req.pk, "처리 시간 초과")


def fail_stale_requests() -> int:
    cutoff = timezone.now() - _timeout()
    ids = list(VerdictRequest.objects.annotate(active_since=Coalesce("started_at", "created_at")).filter(status__in=ACTIVE, active_since__lt=cutoff).values_list("pk", flat=True))
    return sum(fail_request(pk, "처리 시간 초과") for pk in ids)

def complete_analysis(request_id, *, summary, conflict_core, user_speaker_side='RIGHT'):
    if (not isinstance(summary, str) or not summary.strip()
            or not isinstance(conflict_core, str) or not conflict_core.strip()
            or len(conflict_core.strip()) > 255
            or user_speaker_side not in CaseSituation.UserSpeakerSide.values):
        fail_request(request_id, 'AI 상황 분석 결과가 올바르지 않습니다.')
        return False
    with transaction.atomic():
        req = _locked_request(request_id)
        if req.status not in ACTIVE or req.stage != 'ANALYSIS':
            return False
        situation = CaseSituation.objects.get(case_id=req.case_id)
        situation.summary = summary.strip()
        situation.user_speaker_side = user_speaker_side
        situation.analysis_status = 'DONE'
        situation.save(update_fields=['summary', 'user_speaker_side', 'analysis_status', 'updated_at'])
        CaseIssue.objects.filter(case_id=req.case_id).delete()
        CaseIssue.objects.create(case_id=req.case_id, content=conflict_core.strip())
        req.status = 'AWAITING_CONFIRMATION'
        req.save(update_fields=['status'])
        Case.objects.filter(pk=req.case_id).update(status='CONFIRMING', updated_at=timezone.now())
    return True

# 성공/실패/환불/분석원문저장/결과검증

import json
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models.functions import Coalesce
from django.utils import timezone

from cases.models import Case, CaseIssue, CaseSituation
from coins.services import restore_for_verdict, spend_for_verdict

from .models import Verdict, VerdictReason, VerdictRequest, VerdictFactor, VerdictFactorSource, VerdictReply


ACTIVE = ("PENDING", "RUNNING")

# --- 새 함수(접수/판결 시작/취소)에서 쓰는 상태·단계 값 ---
PENDING = "PENDING"
RUNNING = "RUNNING"
AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
DONE = "DONE"
CANCELED = "CANCELED"
STAGE_ANALYSIS = "ANALYSIS"
STAGE_JUDGMENT = "JUDGMENT"
# 같은 사건에 아래 상태의 요청이 있으면 새 접수(=이중 차감)를 막는다.
BLOCKING = (PENDING, RUNNING, AWAITING_CONFIRMATION, DONE)


class AlreadyRequested(Exception):
    """같은 사건에 이미 진행 중이거나 완료된 요청이 있음 (이중 차감 방지)"""

    def __init__(self, request):
        super().__init__("이미 접수된 사건입니다.")
        self.request = request


def _locked_request(request_id):
    # 모든 작업에서 사건 → 요청 순서로 잠근다.
    case_id = VerdictRequest.objects.values_list(
        "case_id",
        flat=True,
    ).get(pk=request_id)

    Case.objects.select_for_update().get(pk=case_id)
    return VerdictRequest.objects.select_for_update().get(pk=request_id)


def _timeout():
    return timedelta(
        seconds=getattr(settings, "VERDICT_TIMEOUT_SECONDS", 600)
    )


def _abandon_after():
    return timedelta(
        days=getattr(settings, "VERDICT_ABANDON_DAYS", 30)
    )


def complete_request(
    request_id,
    *,
    fault_ratio,
    judgment_text,
    reasons=None,
    factors=None,
    title="",
    relation="",
    recommended_reply="",
    public_title="",
    public_summary="",
    input_snapshot=None,
    **optional,
):
    if factors is not None:
        from .results import StoredJudgmentSerializer
        result = StoredJudgmentSerializer(data={
            "title": title, "relation": relation,
            "fault_ratio": fault_ratio, "judgment_text": judgment_text,
            "one_line": optional.get("one_line", ""), "case_summary": optional.get("case_summary", ""),
            "recommended_reply": recommended_reply,
            "public_title": public_title, "public_summary": public_summary,
            "input_snapshot": input_snapshot, "factors": factors,
        })
        if not result.is_valid():
            fail_request(request_id, "AI 판결 결과가 올바르지 않습니다.")
            return False
        factors = result.validated_data["factors"]
        reasons = [factor["summary"] for factor in factors]
    allowed_optional = {
        "one_line",
        "case_summary",
        "recommendation",
    }

    valid = (
        type(fault_ratio) is int
        and 0 <= fault_ratio <= 100
        and isinstance(judgment_text, str)
        and bool(judgment_text.strip())
        and isinstance(reasons, list)
        and bool(reasons)
        and all(
            isinstance(reason, str) and reason.strip()
            for reason in reasons
        )
        and not (set(optional) - allowed_optional)
        and all(isinstance(value, str) for value in optional.values())
        and len(optional.get("one_line", "")) <= 255
    )
    if not valid:
        fail_request(request_id, "AI 판결 결과가 올바르지 않습니다.")
        return False

    with transaction.atomic():
        req = _locked_request(request_id)

        if req.status not in ACTIVE or req.stage != "JUDGMENT":
            return False

        verdict = Verdict(
            request=req,
            case_id=req.case_id,
            title=title, relation=relation, input_snapshot=input_snapshot or {},
            public_title=public_title, public_summary=public_summary,
            fault_ratio=fault_ratio,
            judgment_text=judgment_text.strip(),
            **optional,
        )
        verdict.full_clean()
        verdict.save()

        VerdictReason.objects.bulk_create([
            VerdictReason(
                verdict=verdict,
                content=reason.strip(),
                sort_order=index,
            )
            for index, reason in enumerate(reasons)
        ])

        for index, factor in enumerate(factors or []):
            item = VerdictFactor.objects.create(
                verdict=verdict, key=factor["key"], side=factor["side"],
                name=factor["name"], summary=factor["summary"],
                evidence=factor["evidence"], sort_order=index,
            )
            if factor.get("source"):
                VerdictFactorSource.objects.create(factor=item, **factor["source"])
        if recommended_reply:
            VerdictReply.objects.create(verdict=verdict, content=recommended_reply)
        req.status = "DONE"
        req.progress_step = "DONE"
        req.finished_at = timezone.now()
        req.save(update_fields=[
            "status",
            "progress_step",
            "finished_at",
        ])

        Case.objects.filter(pk=req.case_id).update(
            **({'title': title} if title else {}),
            status=Case.Status.JUDGED,
            updated_at=timezone.now(),
        )

    return True


def fail_request(request_id, reason):
    with transaction.atomic():
        req = _locked_request(request_id)

        if req.status not in ACTIVE:
            return False

        req.status = "FAILED"
        req.progress_step = "FAILED"
        req.error_message = str(reason)[:1000]
        req.finished_at = timezone.now()
        req.save(update_fields=[
            "status",
            "progress_step",
            "error_message",
            "finished_at",
        ])

        # 기존 코인 서비스의 중복 환불 방지 로직을 사용한다.
        restore_for_verdict(verdict_request=req)

        if req.stage == "ANALYSIS":
            CaseSituation.objects.filter(
                case_id=req.case_id,
            ).update(
                analysis_status="FAILED",
                updated_at=timezone.now(),
            )
            case_status = Case.Status.WRITING
        else:
            case_status = Case.Status.READY

        Case.objects.filter(pk=req.case_id).update(
            status=case_status,
            updated_at=timezone.now(),
        )

    return True


def expire_if_stale(req):
    active_since = req.started_at or req.created_at

    if (
        req.status in ACTIVE
        and active_since < timezone.now() - _timeout()
    ):
        fail_request(req.pk, "처리 시간 초과")


def fail_stale_requests():
    cutoff = timezone.now() - _timeout()

    request_ids = list(
        VerdictRequest.objects.annotate(
            active_since=Coalesce("started_at", "created_at"),
        ).filter(
            status__in=ACTIVE,
            active_since__lt=cutoff,
        ).values_list("pk", flat=True)
    )

    return sum(
        fail_request(request_id, "처리 시간 초과")
        for request_id in request_ids
    )


def complete_analysis(
    request_id,
    *,
    summary,
    conflict_core,
    user_speaker_side="RIGHT",
    ai_raw=None,
):
    valid = (
        isinstance(summary, str)
        and bool(summary.strip())
        and isinstance(conflict_core, str)
        and bool(conflict_core.strip())
        and len(conflict_core.strip()) <= 255
        and user_speaker_side in CaseSituation.UserSpeakerSide.values
        and (ai_raw is None or isinstance(ai_raw, dict))
    )
    if not valid:
        fail_request(request_id, "AI 상황 분석 결과가 올바르지 않습니다.")
        return False

    serialized_raw = (
        json.dumps(ai_raw, ensure_ascii=False)
        if ai_raw is not None
        else None
    )

    with transaction.atomic():
        req = _locked_request(request_id)

        if req.status not in ACTIVE or req.stage != "ANALYSIS":
            return False

        situation = (
            CaseSituation.objects.select_for_update()
            .filter(case_id=req.case_id)
            .order_by("-id")
            .first()
        )
        if situation is None:
            raise ValueError("사건의 상황 정보가 없습니다.")

        situation.summary = summary.strip()
        situation.user_speaker_side = user_speaker_side
        situation.analysis_status = "DONE"
        situation.confirmed_at = None

        update_fields = [
            "summary",
            "user_speaker_side",
            "analysis_status",
            "confirmed_at",
            "updated_at",
        ]

        if serialized_raw is not None:
            situation.ai_raw = serialized_raw
            update_fields.append("ai_raw")

        situation.save(update_fields=update_fields)

        CaseIssue.objects.filter(case_id=req.case_id).delete()
        CaseIssue.objects.create(
            case_id=req.case_id,
            content=conflict_core.strip(),
        )

        req.status = "AWAITING_CONFIRMATION"
        req.progress_step = "WAITING_CONFIRMATION"
        req.save(update_fields=["status", "progress_step"])

        Case.objects.filter(pk=req.case_id).update(
            status=Case.Status.CONFIRMING,
            updated_at=timezone.now(),
        )

    return True


# ------------------------------------------------------------------
# 접수 / 판결 시작 / 취소 / 방치 만료 (코인 연동)
# ------------------------------------------------------------------
def start_request(*, user, case):
    """
    사건 접수: 요청 생성 + 1톨 차감을 한 트랜잭션으로 처리한다.
    - 잔액이 부족하면 InsufficientCoins가 발생하고 아무것도 저장되지 않는다.
    - 같은 사건에 진행 중/완료 요청이 있으면 AlreadyRequested → 차감하지 않는다 (중복 클릭 방지).
    - 사건 상태는 바꾸지 않는다 (분석이 끝나면 complete_analysis가 CONFIRMING으로 바꾼다).
    started_at = 현재 단계가 시작된 시각 (시간 초과 판단 기준).
    """
    with transaction.atomic():
        # 사건 → 요청 → 지갑 순서로 잠근다 (_locked_request와 같은 순서)
        Case.objects.select_for_update().get(pk=case.pk)

        existing = (
            VerdictRequest.objects.filter(case_id=case.pk, status__in=BLOCKING)
            .order_by("-id")
            .first()
        )
        if existing is not None:
            raise AlreadyRequested(existing)

        req = VerdictRequest.objects.create(
            case=case,
            user=user,
            status=PENDING,
            stage=STAGE_ANALYSIS,
            progress_step=STAGE_ANALYSIS,
            started_at=timezone.now(),
        )
        spend_for_verdict(user=user, verdict_request=req)
    return req


def mark_running(request_id) -> bool:
    """작업자가 AI 호출 직전에 호출. 이미 취소·실패 등으로 닫힌 요청이면 False → AI를 호출하지 않는다."""
    with transaction.atomic():
        req = _locked_request(request_id)
        if req.status != PENDING:
            return False
        req.status = RUNNING
        req.save(update_fields=["status"])
    return True


def start_verdict(request_id) -> bool:
    """
    '이대로 판결받기': 상황확인 대기 요청을 같은 요청의 판결 단계로 넘긴다. 추가 차감은 없다.
    (새 요청을 만들면 접수 때의 차감과 연결이 끊겨 실패 시 코인이 반환되지 않는다.)
    True일 때만 판결 작업을 시작할 것 (중복 클릭이면 False).
    """
    with transaction.atomic():
        req = _locked_request(request_id)
        if req.status != AWAITING_CONFIRMATION or req.stage != STAGE_ANALYSIS:
            return False

        req.status = PENDING
        req.stage = STAGE_JUDGMENT
        req.progress_step = STAGE_JUDGMENT
        req.started_at = timezone.now()  # 판결 단계의 시간 초과 기준을 새로 시작 (확인 대기 시간은 제외)
        req.save(update_fields=["status", "stage", "progress_step", "started_at"])

        judging = getattr(Case.Status, "JUDGING", None)
        if judging is not None:
            Case.objects.filter(pk=req.case_id).update(
                status=judging,
                updated_at=timezone.now(),
            )
    return True


def cancel_request(request_id) -> bool:
    """
    '새로 시작': 확인 대기 중인 기존 사건을 중단한다. 코인은 반환하지 않는다.
    분석·판결이 진행 중이면 False ('분석이 끝나면 새로 시작할 수 있어요' 안내).
    """
    with transaction.atomic():
        req = _locked_request(request_id)
        if req.status != AWAITING_CONFIRMATION:
            return False
        req.status = CANCELED
        req.progress_step = CANCELED
        req.finished_at = timezone.now()
        req.save(update_fields=["status", "progress_step", "finished_at"])
    return True


def expire_abandoned():
    """접수 후 VERDICT_ABANDON_DAYS일이 지난 확인 대기 사건을 CANCELED(미반환) 처리하고, 처리한 사건 ID 목록을 반환한다."""
    cutoff = timezone.now() - _abandon_after()

    rows = list(
        VerdictRequest.objects.annotate(
            active_since=Coalesce("started_at", "created_at"),
        ).filter(
            status=AWAITING_CONFIRMATION,
            active_since__lt=cutoff,
        ).values_list("pk", "case_id")
    )

    return [case_id for request_id, case_id in rows if cancel_request(request_id)]
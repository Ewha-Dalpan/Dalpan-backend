# 성공/실패/환불/분석원문저장/결과검증

import json
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models.functions import Coalesce
from django.utils import timezone

from cases.models import Case, CaseIssue, CaseSituation
from coins.services import restore_for_verdict

from .models import Verdict, VerdictReason, VerdictRequest, VerdictFactor, VerdictFactorSource, VerdictReply


ACTIVE = ("PENDING", "RUNNING")


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
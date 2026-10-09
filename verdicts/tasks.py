# ai호출

import json
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.module_loading import import_string
from rest_framework.exceptions import APIException

from cases.models import Case, CaseSituation

from .ai_client import (
    AIClientError,
    AnalysisResultSerializer,
    analyze_case,
    judge_case,
)
from .models import VerdictRequest
from .services import (
    _locked_request,
    complete_analysis,
    complete_request,
    expire_if_stale,
    fail_request,
)


logger = logging.getLogger(__name__)


class AIUnavailable(APIException):
    status_code = 503
    default_detail = "AI 작업 연결이 아직 준비되지 않았습니다."


def get_dispatcher(stage):
    path = getattr(settings, f"VERDICT_{stage}_DISPATCHER", None)

    if not path or not getattr(settings, "LINER_API_KEY", ""):
        raise AIUnavailable()

    return import_string(path)


def dispatch_after_commit(dispatcher, request_id):
    try:
        dispatcher(request_id)
    except Exception:
        # 예외 본문에 외부 응답이나 민감한 정보가 있을 수 있어 출력하지 않는다.
        logger.error("AI dispatch failed: request_id=%s", request_id)
        fail_request(request_id, "AI 작업을 시작하지 못했습니다.")


def dispatch_analysis(request_id):
    _check_pending_request(request_id, "ANALYSIS")


def dispatch_judgment(request_id):
    _check_pending_request(request_id, "JUDGMENT")


def _check_pending_request(request_id, stage):
    # VerdictRequest 행 자체가 영속적인 작업 대기열이다.
    # 여기서는 AI를 호출하지 않고 워커가 처리할 요청인지 확인한다.
    # 커밋 직후 워커가 먼저 처리할 수 있다. 상태나 단계 변화는 실패 사유가 아니다.
    if not VerdictRequest.objects.filter(pk=request_id).exists():
        raise ValueError("AI 요청이 없습니다.")



def _claim_request(request_id):
    with transaction.atomic():
        req = _locked_request(request_id)

        if req.status != "PENDING":
            return None

        if req.stage not in ["ANALYSIS", "JUDGMENT"]:
            raise ValueError("알 수 없는 AI 작업 단계입니다.")

        # 기존 접수 시각을 유지하여 대기 시간도 제한에 포함한다.
        req.status = "RUNNING"
        req.progress_step = (
            "ANALYZING_IMAGES"
            if req.stage == "ANALYSIS"
            else "GENERATING_JUDGMENT"
        )
        if req.started_at is None:
            req.started_at = timezone.now()

        req.save(update_fields=[
            "status",
            "progress_step",
            "started_at",
        ])

        return req


def _can_save_result(request_id, stage):
    req = VerdictRequest.objects.get(pk=request_id)

    # AI 응답 중 시간이 초과되었으면 결과를 저장하지 않는다.
    expire_if_stale(req)
    req.refresh_from_db()

    return req.status == "RUNNING" and req.stage == stage


def _check_progress(request_id, stage):
    req = VerdictRequest.objects.get(pk=request_id)
    expire_if_stale(req)
    req.refresh_from_db()
    if req.status != "RUNNING" or req.stage != "JUDGMENT":
        raise AIClientError("판결 요청이 종료되었습니다.")
    elapsed = (timezone.now() - (req.started_at or req.created_at)).total_seconds()
    remaining = settings.VERDICT_TIMEOUT_SECONDS - elapsed
    if remaining <= 10:
        raise AIClientError("처리 시간 초과")
    VerdictRequest.objects.filter(pk=request_id, status="RUNNING", stage="JUDGMENT").update(progress_step=stage)
    return min(settings.LINER_HTTP_TIMEOUT_SECONDS, max(1, remaining - 5))

def process_request(request_id):
    initial_req = VerdictRequest.objects.get(pk=request_id)
    expire_if_stale(initial_req)

    req = _claim_request(request_id)
    if req is None:
        return

    try:
        case = Case.objects.get(pk=req.case_id)
        if case.deleted_at is not None:
            raise AIClientError("삭제된 사건은 분석할 수 없습니다.")

        situation = (
            CaseSituation.objects.filter(case_id=req.case_id)
            .order_by("-id")
            .first()
        )
        if situation is None:
            raise AIClientError("사건의 상황 정보가 없습니다.")

        if req.stage == "ANALYSIS":
            images = list(case.images.order_by("sort_order", "id"))

            result = analyze_case(
                relation=situation.relation,
                images=images,
            )

            if not _can_save_result(req.pk, "ANALYSIS"):
                return

            complete_analysis(
                req.pk,
                summary=result["summary"],
                conflict_core=result["conflict_core"],
                # AI가 본인 화자 위치를 추측하지 않게 한다.
                user_speaker_side=situation.user_speaker_side,
                ai_raw=result,
            )

        else:
            if (
                situation.analysis_status != "DONE"
                or situation.confirmed_at is None
            ):
                raise AIClientError("상황 확인이 완료되지 않았습니다.")

            issue = case.issues.order_by("sort_order", "id").first()
            if issue is None:
                raise AIClientError("갈등 핵심 정보가 없습니다.")

            try:
                raw_analysis = json.loads(situation.ai_raw or "")
            except (TypeError, ValueError):
                raise AIClientError(
                    "저장된 대화 분석이 없습니다. 새 사건으로 접수해 주세요."
                ) from None

            analysis_serializer = AnalysisResultSerializer(
                data=raw_analysis,
            )
            if not analysis_serializer.is_valid():
                raise AIClientError("저장된 대화 분석 형식이 올바르지 않습니다.")

            analysis = analysis_serializer.validated_data
            if not analysis["analyzable"]:
                raise AIClientError("분석 가능한 대화 내용이 없습니다.")

            result = judge_case(
                situation=situation,
                conflict_core=issue.content,
                analysis=analysis,
                check_progress=lambda step: _check_progress(req.pk, step),
            )

            if not _can_save_result(req.pk, "JUDGMENT"):
                return

            complete_request(
                req.pk,
                fault_ratio=result["fault_ratio"],
                judgment_text=result["judgment_text"],
                factors=result["factors"],
                title=result["title"], relation=situation.relation,
                recommended_reply=result["recommended_reply"],
                public_title=result["public_title"], public_summary=result["public_summary"],
                input_snapshot=result["input_snapshot"],
                one_line=result["one_line"],
                case_summary=result["case_summary"],
                recommendation=result["recommended_reply"],
            )

    except AIClientError as exc:
        logger.warning(
            "AI request failed: request_id=%s, stage=%s",
            req.pk,
            req.stage,
        )
        fail_request(req.pk, str(exc))

    except Exception as exc:
        logger.error(
            "AI processing error: request_id=%s, stage=%s, error_type=%s",
            req.pk,
            req.stage,
            type(exc).__name__,
        )
        fail_request(req.pk, "AI 처리 중 오류가 발생했습니다.")
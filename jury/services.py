from datetime import datetime, time

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied

from cases.models import Case, CaseImage
from coins.models import CoinTxType, CoinWallet
from coins.services import change_coins

from .models import CommentStatus, JuryComment, JuryVote, Report, ReportTarget

JURY_REWARD = 1
JURY_DAILY_REWARD_LIMIT = 5  # 하루 코인 지급 한도(한국 시간 자정 기준). 넘어도 참여는 가능

class AlreadyVoted(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "이미 판결을 제출한 사건입니다."
    default_code = "already_voted"
    
class AlreadyReported(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "이미 신고한 대상입니다."
    default_code = "already_reported"


def jury_cases():
    """배심원석에 노출되는 사건: 공개 + 판결 완료 + 숨김/삭제 아님"""
    return Case.objects.filter(
        is_public=True, status=Case.Status.JUDGED, is_hidden=False, deleted_at__isnull=True,
    )

def public_images():
    """배심원석에 보여줄 이미지: 작성자가 탈퇴하면 즉시 비공개(파일은 30일 뒤 삭제)"""
    return CaseImage.objects.filter(case__user__withdrawn_at__isnull=True)

def jury_result(case):
    """전체 배심원 판결. 작성자 쪽만 반올림하고 상대는 100에서 빼서 합이 항상 100"""
    if case.jury_count == 0:
        return None
    author = (2 * case.fault_ratio_sum + case.jury_count) // (2 * case.jury_count)  # 0.5는 올림
    return {"author": author, "other": 100 - author}


def my_vote(user, case):
    if not user.is_authenticated:
        return None
    return JuryVote.objects.filter(case=case, user=user).first()


def can_view_result(user, case, vote=None):
    """판결 제출자 또는 사건 작성자만 결과/댓글 열람 가능"""
    if not user.is_authenticated:
        return False
    if case.user_id == user.pk:
        return True
    return (vote or my_vote(user, case)) is not None


def _today_start():
    today = timezone.localdate()  # settings.TIME_ZONE = Asia/Seoul 기준 날짜
    return datetime.combine(today, time.min, tzinfo=timezone.get_current_timezone())


@transaction.atomic
def submit_vote(*, user, case_id, fault_ratio):
    # 사건 행을 잠가서 같은 사건에 동시 제출해도 집계값이 꼬이지 않게 함
    case = get_object_or_404(jury_cases().select_for_update(), pk=case_id)
    if case.user_id == user.pk:
        raise PermissionDenied("본인 사건에는 판결을 제출할 수 없습니다.")

    # 지갑 행을 잠근 뒤 오늘 지급 횟수를 세야 동시 요청으로 한도를 넘지 않음
    CoinWallet.objects.get_or_create(user=user)
    CoinWallet.objects.select_for_update().get(user=user)
    rewarded_today = JuryVote.objects.filter(
        user=user, coin_rewarded=True, created_at__gte=_today_start(),
    ).count()
    reward = rewarded_today < JURY_DAILY_REWARD_LIMIT

    try:
        with transaction.atomic():
            vote = JuryVote.objects.create(
                case=case, user=user, fault_ratio=fault_ratio, coin_rewarded=reward,
            )
    except IntegrityError:
        raise AlreadyVoted()

    Case.objects.filter(pk=case.pk).update(
        jury_count=F("jury_count") + 1,
        fault_ratio_sum=F("fault_ratio_sum") + fault_ratio,
    )
    if reward:
        change_coins(
            user=user, tx_type=CoinTxType.JURY_REWARD, amount=JURY_REWARD,
            idempotency_key=f"jury-reward:{vote.pk}", case=case,
        )
    case.refresh_from_db(fields=["jury_count", "fault_ratio_sum"])
    return case, vote


def viewable_case(user, case_id):
    """댓글 API용: 결과 열람 권한이 없으면 403"""
    case = get_object_or_404(jury_cases(), pk=case_id)
    if not can_view_result(user, case):
        raise PermissionDenied("판결을 제출한 배심원만 볼 수 있습니다.")
    return case


def create_comment(*, user, case_id, content):
    case = viewable_case(user, case_id)  # 판결 제출자 또는 작성자
    return JuryComment.objects.create(case=case, user=user, content=content)


def delete_comment(*, user, case_id, comment_id):
    comment = get_object_or_404(
        JuryComment, pk=comment_id, case_id=case_id, user=user, status=CommentStatus.VISIBLE,
    )
    comment.status = CommentStatus.DELETED
    comment.deleted_at = timezone.now()
    comment.save(update_fields=["status", "deleted_at", "updated_at"])
    
def _create_report(**fields):
    try:
        with transaction.atomic():
            return Report.objects.create(**fields)
    except IntegrityError:
        raise AlreadyReported()


def report_case(*, user, case_id, reason, detail):
    case = get_object_or_404(jury_cases(), pk=case_id)
    if case.user_id == user.pk:
        raise PermissionDenied("본인 사건은 신고할 수 없습니다.")
    return _create_report(reporter=user, target_type=ReportTarget.CASE, case=case, reason=reason, detail=detail)


def report_comment(*, user, case_id, comment_id, reason, detail):
    case = viewable_case(user, case_id)  # 댓글을 볼 수 있는 사람만 신고 가능
    comment = get_object_or_404(JuryComment, pk=comment_id, case=case, status=CommentStatus.VISIBLE)
    if comment.user_id == user.pk:
        raise PermissionDenied("본인 댓글은 신고할 수 없습니다.")
    return _create_report(
        reporter=user, target_type=ReportTarget.COMMENT, comment=comment, reason=reason, detail=detail,
    )
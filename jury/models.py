from django.conf import settings
from django.core.validators import MaxValueValidator
from django.db import models
from django.db.models import Q


class JuryVote(models.Model):
    case = models.ForeignKey("cases.Case", on_delete=models.PROTECT, related_name="jury_votes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="jury_votes")
    fault_ratio = models.PositiveSmallIntegerField(validators=[MaxValueValidator(100)])  # 작성자 과실 비율(%), 수정 불가
    coin_rewarded = models.BooleanField(default=False)  # 하루 한도 초과 시 False
    created_at = models.DateTimeField(auto_now_add=True)  # 화면 미노출, 한도/주목 사건 계산용

    class Meta:
        db_table = "jury_votes"
        constraints = [
            models.UniqueConstraint(fields=["case", "user"], name="uq_vote_case_user"),
            models.CheckConstraint(
                condition=Q(fault_ratio__gte=0) & Q(fault_ratio__lte=100), name="ck_vote_ratio_0_100"
            ),
        ]
        indexes = [
            models.Index(fields=["user", "created_at"], name="ix_vote_user_time"),
            models.Index(fields=["case", "created_at"], name="ix_vote_case_time"),
        ]


class CommentStatus(models.TextChoices):
    VISIBLE = "VISIBLE", "표시"
    HIDDEN = "HIDDEN", "관리자 숨김"
    DELETED = "DELETED", "본인 삭제"


class JuryComment(models.Model):
    case = models.ForeignKey("cases.Case", on_delete=models.PROTECT, related_name="jury_comments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="jury_comments")
    content = models.CharField(max_length=1000)
    status = models.CharField(max_length=10, choices=CommentStatus.choices, default=CommentStatus.VISIBLE)
    hidden_reason = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "jury_comments"
        indexes = [
            models.Index(fields=["case", "created_at"], name="ix_comment_case_time"),
            models.Index(fields=["user", "created_at"], name="ix_comment_user_time"),
        ]


class ReportTarget(models.TextChoices):
    COMMENT = "COMMENT", "댓글"
    CASE = "CASE", "사건"


class ReportReason(models.TextChoices):
    ABUSE = "ABUSE", "욕설/비방"
    PRIVACY = "PRIVACY", "개인정보 노출"
    SPAM = "SPAM", "스팸"
    OTHER = "OTHER", "기타"


class ReportStatus(models.TextChoices):
    PENDING = "PENDING", "대기"
    RESOLVED = "RESOLVED", "처리 완료"
    REJECTED = "REJECTED", "반려"


class Report(models.Model):
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reports_made")
    target_type = models.CharField(max_length=10, choices=ReportTarget.choices)
    comment = models.ForeignKey(JuryComment, null=True, blank=True, on_delete=models.PROTECT, related_name="reports")
    case = models.ForeignKey("cases.Case", null=True, blank=True, on_delete=models.PROTECT, related_name="reports")
    reason = models.CharField(max_length=20, choices=ReportReason.choices)
    detail = models.CharField(max_length=500, null=True, blank=True)
    status = models.CharField(max_length=10, choices=ReportStatus.choices, default=ReportStatus.PENDING)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reports_resolved"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "reports"
        constraints = [
            models.UniqueConstraint(fields=["reporter", "comment"], name="uq_report_comment"),
            models.UniqueConstraint(fields=["reporter", "case"], name="uq_report_case"),
            models.CheckConstraint(
                condition=(
                    Q(target_type="COMMENT", comment__isnull=False, case__isnull=True)
                    | Q(target_type="CASE", case__isnull=False, comment__isnull=True)
                ),
                name="ck_report_target",
            ),
        ]
        indexes = [models.Index(fields=["status", "created_at"], name="ix_report_status")]
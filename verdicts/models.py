from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class VerdictRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', '처리대기'
        RUNNING = 'RUNNING', '처리중'
        DONE = 'DONE', '완료'
        FAILED = 'FAILED', '실패'
        AWAITING_CONFIRMATION = 'AWAITING_CONFIRMATION', '상황 확인 대기'

    case = models.ForeignKey(
        'cases.Case',
        on_delete=models.CASCADE,
        related_name='verdict_requests',
    )
    user = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='verdict_requests',
    )
    class Stage(models.TextChoices):
        ANALYSIS = 'ANALYSIS', '상황 분석'
        JUDGMENT = 'JUDGMENT', '판결 생성'

    stage = models.CharField(max_length=10, choices=Stage.choices, default=Stage.JUDGMENT)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)
    progress_step = models.CharField(max_length=30, null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'verdict_requests'


class Verdict(models.Model):
    request = models.ForeignKey(
        VerdictRequest,
        on_delete=models.CASCADE,
        related_name='verdicts',
    )
    case = models.ForeignKey('cases.Case', on_delete=models.CASCADE, related_name='verdicts')
    fault_ratio = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )#과실비율
    one_line = models.CharField(max_length=255, null=True, blank=True) #갈등핵심
    case_summary = models.TextField(null=True, blank=True)#갈등상황요약
    judgment_text = models.TextField()#판결문
    recommendation = models.TextField(null=True, blank=True)#추천 문구
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'verdicts'
        constraints = [
            models.CheckConstraint(
                condition=models.Q(fault_ratio__gte=0, fault_ratio__lte=100),
                name='verdict_fault_ratio_0_100',
            ),
        ]


class VerdictReply(models.Model):
    verdict = models.ForeignKey(Verdict, on_delete=models.CASCADE, related_name='replies')
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'verdict_replies'


class VerdictReason(models.Model):
    verdict = models.ForeignKey(Verdict, on_delete=models.CASCADE, related_name='reasons')
    content = models.TextField()
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = 'verdict_reasons'

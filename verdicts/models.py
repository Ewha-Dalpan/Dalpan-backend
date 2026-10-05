from django.db import models


class VerdictRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', '처리대기'
        RUNNING = 'RUNNING', '처리중'
        DONE = 'DONE', '완료'
        FAILED = 'FAILED', '실패'

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
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
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
    fault_ratio = models.PositiveSmallIntegerField()#과실비율
    one_line = models.CharField(max_length=255, null=True, blank=True) #갈등핵심
    case_summary = models.TextField(null=True, blank=True)#갈등상황요약
    judgment_text = models.TextField()#판결문
    recommendation = models.TextField(null=True, blank=True)#추천 문구
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'verdicts'


class VerdictReply(models.Model):
    class Tone(models.TextChoices):
        KIND = 'KIND', '다정한'
        FIRM = 'FIRM', '단호한'
        COOL = 'COOL', '담백한'

    verdict = models.ForeignKey(Verdict, on_delete=models.CASCADE, related_name='replies')
    tone = models.CharField(max_length=10, choices=Tone.choices)
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

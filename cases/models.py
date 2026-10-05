from pathlib import Path
from uuid import uuid4 #파일 이름 생성시 uuid4를 사용하여 중복 방지

from django.conf import settings
from django.db import models


def case_image_path(instance, filename):
    extension = Path(filename).suffix.lower()
    return f"cases/{instance.case_id}/{uuid4().hex}{extension}"


class Case(models.Model):
    class Status(models.TextChoices):
        WRITING = 'WRITING', '작성중'
        CONFIRMING = 'CONFIRMING', '상황 확인중'
        READY = 'READY', '판결 준비완료'
        JUDGING = 'JUDGING', '판결 진행중'
        JUDGED = 'JUDGED', '판결 완료'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='cases',
    )
    category_id = models.BigIntegerField(null=True, blank=True)
    title = models.CharField(max_length=100, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.WRITING)
    is_public = models.BooleanField(default=False)
    public_at = models.DateTimeField(null=True, blank=True)
    is_hidden = models.BooleanField(default=False)
    jury_count = models.IntegerField(default=0)
    fault_ratio_sum = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'cases'


class CaseImage(models.Model):
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name='images')
    image_key = models.ImageField(upload_to=case_image_path, max_length=255)
    sort_order = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'case_images'
        ordering = ['sort_order', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['case', 'sort_order'],
                name='unique_case_image_sort_order',
            )
        ]


class CaseSituation(models.Model):
    class AnalysisStatus(models.TextChoices):
        PENDING = 'PENDING', '대기'
        RUNNING = 'RUNNING', '분석중'
        DONE = 'DONE', '완료'
        FAILED = 'FAILED', '실패'

    class UserSpeakerSide(models.TextChoices):
        LEFT = 'LEFT', '왼쪽'
        RIGHT = 'RIGHT', '오른쪽'

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name='situations')
    relation = models.CharField(max_length=50, null=True, blank=True)
    speakers = models.TextField(null=True, blank=True)
    user_speaker_side = models.CharField(
        max_length=5,
        choices=UserSpeakerSide.choices,
        default=UserSpeakerSide.RIGHT,
    )
    summary = models.TextField(null=True, blank=True)
    ai_raw = models.TextField(null=True, blank=True)
    user_extra_context = models.TextField(null=True, blank=True)
    analysis_status = models.CharField(
        max_length=20,
        choices=AnalysisStatus.choices,
        default=AnalysisStatus.PENDING,
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'case_situations'


class CaseIssue(models.Model):
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name='issues')
    content = models.CharField(max_length=255)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = 'case_issues'

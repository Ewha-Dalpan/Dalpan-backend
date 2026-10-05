from pathlib import Path
from uuid import uuid4 #파일 이름 생성시 uuid4를 사용하여 중복 방지

from django.conf import settings
from django.db import models

def case_image_path(instance, filename):
    extension = Path(filename).suffix.lower()
    return f"cases/{instance.case_id}/{uuid4().hex}{extension}"

class Case(models.Model):
    class Status(models.TextChoices):
        WRITING = 'writing', '작성중'
        CONFIRMING = 'confirming', '사용자 최종 확인중' # 더 수정할 래요 누를 시 수정 가능한 상태
        READY = 'ready', '판결준비완료/AI판결대기' # 좋아요 누를 시 ai 판결 진행 가능하도록 ready로 변경. 톨 부족하면 coins앱으로 연결되도록 진행
        AIJUDGED = 'aijudged', 'AI판결완료' #판결문 생성된 상태
        JUDGING = 'judging', '배심원판결중' # 배심원이 판결을 진행 중인 상태
        JUDGED = 'judged', '배심원판결완료' #배심원 판결 완료.

    class SpeakerSide(models.TextChoices):
        LEFT = 'left', '왼쪽'
        RIGHT = 'right', '오른쪽'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.PROTECT, 
        related_name='cases',
        )

    description = models.TextField(blank=True, default = "")
    relationship = models.CharField(max_length=50,blank=True, default = "")
    opponent_name = models.CharField(max_length=100,blank=True, default = "")
    speaker_side = models.CharField(max_length=5, choices=SpeakerSide.choices, default=SpeakerSide.RIGHT)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.WRITING)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'cases'

class CaseImage(models.Model):
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name='images')
    image_key = models.ImageField(upload_to=case_image_path, max_length=255)
    sort_order = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'case_images'
        ordering = ['sort_order','id']
        constraints = [
            models.UniqueConstraint(
                fields=['case', 'sort_order'],
                name='unique_case_image_sort_order')
        ]
# Create your models here.

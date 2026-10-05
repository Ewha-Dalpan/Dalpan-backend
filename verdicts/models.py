from django.db import models

class VerdictRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', '처리대기'
        PROGRESSING = 'progressing', '처리중'
        COMPLETED = 'completed', '완료'
        FAILED = 'failed', '실패'

    case = models.OneToOneField(
        'cases.Case', 
        on_delete=models.CASCADE, 
        related_name='verdict_request',
        )
    input_data = models.JSONField()

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True) #제출 이후 재판결 할 일은 없어서 일단 만들어두긴했는데 필요있나?싶음. 수정필요.

    class Meta:
        db_table = 'verdict_requests'
# Create your models here.

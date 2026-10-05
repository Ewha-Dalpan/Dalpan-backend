from django.conf import settings
from django.db import models


class Report(models.Model):
	class TargetType(models.TextChoices):
		COMMENT = 'COMMENT', '댓글'
		CASE = 'CASE', '사건'

	class Reason(models.TextChoices):
		ABUSE = 'ABUSE', '욕설/비방'
		PRIVACY = 'PRIVACY', '개인정보'
		SPAM = 'SPAM', '스팸'
		OTHER = 'OTHER', '기타'

	class Status(models.TextChoices):
		PENDING = 'PENDING', '처리 대기'
		RESOLVED = 'RESOLVED', '처리 완료'
		REJECTED = 'REJECTED', '반려'

	reporter = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.PROTECT,
		related_name='reports_submitted',
	)
	target_type = models.CharField(max_length=10, choices=TargetType.choices)
	comment = models.ForeignKey(
		'jury.JuryComment',
		on_delete=models.CASCADE,
		null=True,
		blank=True,
		related_name='reports',
	)
	case = models.ForeignKey(
		'cases.Case',
		on_delete=models.CASCADE,
		null=True,
		blank=True,
		related_name='reports',
	)
	reason = models.CharField(max_length=20, choices=Reason.choices)
	detail = models.CharField(max_length=500, null=True, blank=True)
	status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
	resolved_by = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		db_column='resolved_by',
		related_name='reports_resolved',
	)
	resolved_at = models.DateTimeField(null=True, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		db_table = 'reports'

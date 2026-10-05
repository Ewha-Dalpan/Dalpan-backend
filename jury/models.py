from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class JuryVote(models.Model):
	case = models.ForeignKey('cases.Case', on_delete=models.CASCADE, related_name='jury_votes')
	user = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.PROTECT,
		related_name='jury_votes',
	)
	fault_ratio = models.PositiveSmallIntegerField(
		validators=[MinValueValidator(0), MaxValueValidator(100)],
	)
	coin_rewarded = models.BooleanField(default=False)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		db_table = 'jury_votes'


class JuryComment(models.Model):
	class Status(models.TextChoices):
		VISIBLE = 'VISIBLE', '공개'
		HIDDEN = 'HIDDEN', '관리자 숨김'
		DELETED = 'DELETED', '작성자 삭제'

	case = models.ForeignKey('cases.Case', on_delete=models.CASCADE, related_name='jury_comments')
	user = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.PROTECT,
		related_name='jury_comments',
	)
	content = models.CharField(max_length=1000)
	status = models.CharField(max_length=10, choices=Status.choices, default=Status.VISIBLE)
	hidden_reason = models.CharField(max_length=255, null=True, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)
	deleted_at = models.DateTimeField(null=True, blank=True)

	class Meta:
		db_table = 'jury_comments'

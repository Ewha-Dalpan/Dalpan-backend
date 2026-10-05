from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class LoginType(models.TextChoices):
        KAKAO = 'KAKAO', '카카오'
        LOCAL = 'LOCAL', '일반'

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', '활성'
        SUSPENDED = 'SUSPENDED', '정지'
        DELETED = 'DELETED', '탈퇴'

    first_name = None
    last_name = None
    date_joined = None

    login_type = models.CharField(max_length=10, choices=LoginType.choices)
    kakao_id = models.CharField(max_length=50, null=True, blank=True)
    email = models.EmailField(max_length=254, null=True, blank=True)
    nickname = models.CharField(max_length=30)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspended_reason = models.CharField(max_length=255, null=True, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    REQUIRED_FIELDS = []

    class Meta:
        db_table = 'users'


class UserConsent(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='consents')
    consent_type = models.CharField(max_length=20)
    version = models.CharField(max_length=20)
    agreed_at = models.DateTimeField()

    class Meta:
        db_table = 'user_consents'


class WithdrawnIdentity(models.Model):
    class LoginType(models.TextChoices):
        KAKAO = 'KAKAO', '카카오'
        LOCAL = 'LOCAL', '일반'

    identity_hash = models.CharField(max_length=64)
    login_type = models.CharField(max_length=10, choices=LoginType.choices)
    is_banned = models.BooleanField(default=False)
    withdrawn_at = models.DateTimeField()
    user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        db_column='id2',
        related_name='withdrawn_identity_records',
    )

    class Meta:
        db_table = 'withdrawn_identities'

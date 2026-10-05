import hashlib
import hmac

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class LoginType(models.TextChoices):
    KAKAO = "KAKAO", "카카오"
    LOCAL = "LOCAL", "일반"


class UserStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "정상"
    SUSPENDED = "SUSPENDED", "정지"
    DELETED = "DELETED", "탈퇴"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, username, password=None, **extra_fields):
        if not username:
            raise ValueError("username은 필수입니다.")
        extra_fields.setdefault("login_type", LoginType.LOCAL)
        user = self.model(username=username, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()  # 카카오 회원
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("nickname", "관리자")
        return self.create_user(username, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    login_type = models.CharField(max_length=10, choices=LoginType.choices)
    kakao_id = models.CharField(max_length=50, null=True, blank=True, unique=True)  # 탈퇴 시 NULL
    username = models.CharField(max_length=150, unique=True)  # 카카오: 자동 생성값 / 탈퇴: deleted_<id>
    email = models.EmailField(max_length=254, null=True, blank=True)
    nickname = models.CharField(max_length=30)  # DB unique 없음 (서비스에서 중복 확인)
    status = models.CharField(max_length=10, choices=UserStatus.choices, default=UserStatus.ACTIVE)
    is_active = models.BooleanField(default=True)  # 정지/탈퇴 시 False
    is_staff = models.BooleanField(default=False)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspended_reason = models.CharField(max_length=255, null=True, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # password, last_login: AbstractBaseUser / is_superuser: PermissionsMixin

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "users"
        indexes = [models.Index(fields=["status"], name="ix_users_status")]

    def __str__(self):
        return f"{self.nickname}({self.pk})"


class ConsentType(models.TextChoices):
    TERMS = "TERMS", "서비스 이용약관"
    PRIVACY = "PRIVACY", "개인정보 수집·이용"
    AGE_14 = "AGE_14", "만 14세 이상 확인"


class UserConsent(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="consents")
    consent_type = models.CharField(max_length=20, choices=ConsentType.choices)
    version = models.CharField(max_length=20)
    agreed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "user_consents"
        constraints = [
            models.UniqueConstraint(fields=["user", "consent_type", "version"], name="uq_consent"),
        ]


class WithdrawnIdentity(models.Model):
    identity_hash = models.CharField(max_length=64, unique=True)  # HMAC-SHA256 hex
    login_type = models.CharField(max_length=10, choices=LoginType.choices)
    is_banned = models.BooleanField(default=False)  # 제재 중 탈퇴 → 재가입 차단
    withdrawn_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "withdrawn_identities"

    @staticmethod
    def make_hash(login_type: str, raw_identity: str) -> str:
        """카카오 ID/아이디를 비밀키 기반 HMAC으로 해시. 가입·탈퇴 양쪽에서 이 함수만 쓸 것."""
        msg = f"{login_type}:{raw_identity}".encode("utf-8")
        key = settings.IDENTITY_HASH_KEY.encode("utf-8")
        return hmac.new(key, msg, hashlib.sha256).hexdigest()
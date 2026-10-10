from django.contrib import admin
from django.utils import timezone

from .models import User, UserConsent, UserStatus, WithdrawnIdentity
from .services import revoke_all_tokens


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("id", "nickname", "status", "login_type", "is_active", "is_staff", "created_at")
    list_filter = ("status", "login_type", "is_staff")
    search_fields = ("nickname", "username")  # 코인 지급 화면의 회원 검색에도 쓰인다
    ordering = ("-id",)
    fields = (
        "nickname", "login_type", "username", "status", "suspended_reason", "suspended_at",
        "withdrawn_at", "is_active", "is_staff", "last_login", "created_at",
    )
    readonly_fields = (
        "nickname", "login_type", "username", "suspended_at", "withdrawn_at",
        "is_active", "last_login", "created_at",
    )

    def has_add_permission(self, request):
        return False  # 회원은 카카오 가입으로만 생성 (관리자 계정은 createsuperuser)

    def has_delete_permission(self, request, obj=None):
        return False  # 삭제 대신 탈퇴 API(비식별화)로만 처리

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status == UserStatus.DELETED:
            return self.fields  # 탈퇴한 회원은 수정 불가
        return super().get_readonly_fields(request, obj)

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        if db_field.name == "status":
            kwargs["choices"] = [(UserStatus.ACTIVE, "정상"), (UserStatus.SUSPENDED, "정지")]
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        suspended_now = False
        if change and "status" in form.changed_data:
            if obj.status == UserStatus.SUSPENDED:
                obj.is_active = False
                obj.suspended_at = timezone.now()
                suspended_now = True
            elif obj.status == UserStatus.ACTIVE:
                obj.is_active = True
                obj.suspended_at = None
                obj.suspended_reason = None
        super().save_model(request, obj, form, change)
        if suspended_now:
            revoke_all_tokens(obj.pk)  # 정지 즉시 재발급 불가 (access는 is_active 검사로 차단)


@admin.register(UserConsent)
class UserConsentAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "consent_type", "version", "agreed_at")
    list_filter = ("consent_type", "version")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WithdrawnIdentity)
class WithdrawnIdentityAdmin(admin.ModelAdmin):
    list_display = ("id", "login_type", "is_banned", "withdrawn_at")
    list_filter = ("is_banned", "login_type")
    readonly_fields = ("identity_hash", "login_type", "withdrawn_at")  # is_banned만 수정 가능

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
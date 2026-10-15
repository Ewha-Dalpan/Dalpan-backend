from django.contrib import admin

from .models import Case


@admin.register(Case)
class CaseAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "status", "is_public", "is_hidden", "created_at"]
    list_filter = ["status", "is_public", "is_hidden"]
    actions = ["hide_cases"]

    @admin.action(description="선택한 사건 숨김")
    def hide_cases(self, request, queryset):
        queryset.filter(is_hidden=False).update(is_hidden=True)
from django.contrib import admin
from django.utils import timezone

from .models import CommentStatus, JuryComment, JuryVote, Report, ReportStatus


@admin.register(JuryVote)
class JuryVoteAdmin(admin.ModelAdmin):
    list_display = ["id", "case", "user", "fault_ratio", "coin_rewarded", "created_at"]
    readonly_fields = [f.name for f in JuryVote._meta.fields]  # 집계값과 어긋나지 않게 수정 금지


@admin.register(JuryComment)
class JuryCommentAdmin(admin.ModelAdmin):
    list_display = ["id", "case", "user", "content", "status", "created_at"]
    list_filter = ["status"]
    actions = ["hide_comments"]

    @admin.action(description="선택한 댓글 숨김")
    def hide_comments(self, request, queryset):
        queryset.filter(status=CommentStatus.VISIBLE).update(
            status=CommentStatus.HIDDEN, hidden_reason="신고 처리", updated_at=timezone.now(),
        )


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ["id", "target_type", "case", "comment", "reason", "reporter", "status", "created_at"]
    list_filter = ["status", "target_type", "reason"]
    actions = ["resolve", "reject"]

    def _close(self, request, queryset, status):
        queryset.filter(status=ReportStatus.PENDING).update(
            status=status, resolved_by=request.user, resolved_at=timezone.now(),
        )

    @admin.action(description="처리 완료")
    def resolve(self, request, queryset):
        self._close(request, queryset, ReportStatus.RESOLVED)

    @admin.action(description="반려")
    def reject(self, request, queryset):
        self._close(request, queryset, ReportStatus.REJECTED)
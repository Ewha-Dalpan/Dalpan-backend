from rest_framework import serializers
from django.urls import reverse
from .models import FAULT_RATIO_CHOICES, JuryComment, ReportReason

def image_url(request, case_id, image_id):
    path = reverse("jury-case-image", kwargs={"case_id": case_id, "image_id": image_id})
    return request.build_absolute_uri(path)

class JuryCaseListSerializer(serializers.Serializer):
    """목록/메인 카드 한 장"""
    case_id = serializers.IntegerField(source="pk")
    relation = serializers.CharField()
    title = serializers.CharField(source="public_title")
    jury_count = serializers.IntegerField()
    comment_count = serializers.IntegerField()
    public_at = serializers.DateTimeField()
    thumbnail = serializers.SerializerMethodField()

    def get_thumbnail(self, obj):
        if obj.thumbnail_id is None:
            return None
        return image_url(self.context["request"], obj.pk, obj.thumbnail_id)


class VoteCreateSerializer(serializers.Serializer):
    fault_ratio = serializers.ChoiceField(
        choices=FAULT_RATIO_CHOICES,
        error_messages={"invalid_choice": "과실 비율은 0~100 사이 10 단위로 입력해야 합니다."},
    )  # 작성자 과실(%), 상대는 100 - fault_ratio


class CommentSerializer(serializers.ModelSerializer):
    nickname = serializers.CharField(source="user.nickname", read_only=True)
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = JuryComment
        fields = ["id", "nickname", "content", "created_at", "is_mine"]
        read_only_fields = ["id", "nickname", "created_at", "is_mine"]

    def get_is_mine(self, obj):
        user = self.context["request"].user
        return user.is_authenticated and obj.user_id == user.pk
    
class ReportCreateSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(choices=ReportReason.choices)
    detail = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")

    def validate(self, attrs):
        attrs["detail"] = attrs["detail"].strip() or None
        if attrs["reason"] == ReportReason.OTHER and not attrs["detail"]:
            raise serializers.ValidationError({"detail": "기타 사유는 내용을 입력해야 합니다."})
        return attrs
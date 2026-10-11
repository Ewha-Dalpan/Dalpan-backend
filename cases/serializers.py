from rest_framework import serializers
from django.urls import reverse

from .models import Case, CaseImage, CaseSituation


class CaseImageSerializer(serializers.ModelSerializer):
    image_key = serializers.SerializerMethodField()

    def get_image_key(self, obj):
        path = reverse("case-image-detail", kwargs={"case_id": obj.case_id, "image_id": obj.pk})
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request else path

    class Meta:
        model = CaseImage
        fields = ['id', 'image_key', 'sort_order']


class SituationSerializer(serializers.ModelSerializer):
    class Meta:
        model = CaseSituation
        fields = ['relation', 'user_speaker_side', 'summary', 'analysis_status', 'confirmed_at']


class CaseSerializer(serializers.ModelSerializer):
    images = CaseImageSerializer(many=True, read_only=True)
    situation = serializers.SerializerMethodField()
    conflict_core = serializers.SerializerMethodField()

    class Meta:
        model = Case
        fields = ['id', 'title', 'status', 'created_at', 'updated_at', 'images',
                  'situation', 'conflict_core']
        read_only_fields = fields

    def get_situation(self, obj):
        situation = obj.situations.order_by('-id').first()
        return SituationSerializer(situation).data if situation else None

    def get_conflict_core(self, obj):
        issue = obj.issues.order_by('sort_order', 'id').first()
        return issue.content if issue else None


class CaseCreateSerializer(serializers.Serializer):
    relation = serializers.ChoiceField(
        choices=['연애', '친구', '가족', '직장·학교', '기타'],
    )
    images = serializers.ListField(child=serializers.ImageField(), min_length=1, max_length=6)

    def validate_images(self, images):
        from .uploads import validate_case_images
        validate_case_images(images)
        return images


class SituationUpdateSerializer(serializers.Serializer):
    user_speaker_side = serializers.ChoiceField(choices=CaseSituation.UserSpeakerSide.choices, required=False)
    summary = serializers.CharField(required=False, allow_blank=False, max_length=10000)

    def to_internal_value(self, data):
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key: '수정할 수 없는 항목입니다.' for key in unknown})
        return super().to_internal_value(data)

from rest_framework import serializers
from .models import Verdict, VerdictFactor, VerdictFactorSource, VerdictRequest

class SourceSerializer(serializers.ModelSerializer):

    class Meta:
        model = VerdictFactorSource
        fields = ["kind", "title", "url", "publisher", "authors", "published_date", "reference_summary"]
        read_only_fields = fields

class FactorSerializer(serializers.ModelSerializer):
    source = SourceSerializer(read_only=True)
    can_expand = serializers.SerializerMethodField()

    class Meta:
        model = VerdictFactor
        fields = ["id", "key", "side", "name", "summary", "evidence", "can_expand", "source"]
        read_only_fields = fields

    def get_can_expand(self, obj):
        return hasattr(obj, "source")

class PublicVerdictSerializer(serializers.ModelSerializer):
    case_number = serializers.SerializerMethodField()
    title = serializers.CharField(source="public_title", read_only=True)
    case_summary = serializers.CharField(source="public_summary", read_only=True)
    other_fault_ratio = serializers.SerializerMethodField()

    class Meta:
        model = Verdict
        fields = ["case_id", "case_number", "title", "relation", "case_summary", "fault_ratio", "other_fault_ratio"]
        read_only_fields = fields

    def get_case_number(self, obj):
        return f"제{obj.case_id:05d}호"

    def get_other_fault_ratio(self, obj):
        return 100 - obj.fault_ratio

class VerdictSerializer(serializers.ModelSerializer):
    case_number = serializers.SerializerMethodField()
    other_fault_ratio = serializers.SerializerMethodField()
    recommended_reply = serializers.SerializerMethodField()
    self_factors = serializers.SerializerMethodField()
    other_factors = serializers.SerializerMethodField()
    reasons = serializers.SerializerMethodField()
    public_preview = PublicVerdictSerializer(source="*", read_only=True)
    is_public = serializers.BooleanField(source="case.is_public", read_only=True)

    class Meta:
        model = Verdict
        fields = [
            "id", "case_id", "case_number", "title", "relation", "fault_ratio",
            "other_fault_ratio", "one_line", "case_summary", "judgment_text",
            "recommendation", "recommended_reply", "self_factors", "other_factors",
            "reasons", "public_preview", "is_public", "created_at",
        ]
        read_only_fields = fields

    def get_case_number(self, obj):
        return f"제{obj.case_id:05d}호"

    def get_other_fault_ratio(self, obj):
        return 100 - obj.fault_ratio

    def get_recommended_reply(self, obj):
        replies = list(obj.replies.all())
        return replies[-1].content if replies else (obj.recommendation or "")

    def get_self_factors(self, obj):
        return FactorSerializer([f for f in obj.factors.all() if f.side == "SELF"], many=True).data

    def get_other_factors(self, obj):
        return FactorSerializer([f for f in obj.factors.all() if f.side == "OTHER"], many=True).data

    def get_reasons(self, obj):
        return [reason.content for reason in obj.reasons.all()]

class VerdictRequestSerializer(serializers.ModelSerializer):
    verdict = serializers.SerializerMethodField()

    class Meta:
        model = VerdictRequest
        fields = [
            "id", "case_id", "stage", "status", "progress_step", "error_message",
            "created_at", "started_at", "finished_at", "verdict",
        ]
        read_only_fields = fields

    def get_verdict(self, obj):
        if obj.status != VerdictRequest.Status.DONE:
            return None
        verdict = obj.verdicts.order_by("-id").select_related("case").prefetch_related(
            "factors__source", "reasons", "replies",
        ).first()
        return VerdictSerializer(verdict).data if verdict else None

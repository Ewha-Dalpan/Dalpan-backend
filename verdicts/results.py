"""저장 전에 구조화된 판결 결과를 검증한다."""
from rest_framework import serializers
from .ai_client import FactorSerializer
from .search_client import valid_source_url

class SourceSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["SCHOLAR", "WEB"])
    title = serializers.CharField(max_length=500)
    url = serializers.URLField(max_length=2048)
    publisher = serializers.CharField(max_length=255, allow_blank=True)
    authors = serializers.ListField(child=serializers.CharField(max_length=255), max_length=20)
    published_date = serializers.CharField(max_length=50, allow_blank=True)
    reference_summary = serializers.CharField(max_length=5000)

    def validate_url(self, value):
        if not valid_source_url(value):
            raise serializers.ValidationError("올바른 외부 출처 URL이 아닙니다.")
        return value

class StoredFactorSerializer(FactorSerializer):
    source = SourceSerializer(allow_null=True)

class StoredJudgmentSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=100)
    relation = serializers.CharField(max_length=50)
    fault_ratio = serializers.IntegerField(min_value=0, max_value=100)
    judgment_text = serializers.CharField(max_length=20000)
    one_line = serializers.CharField(max_length=255)
    case_summary = serializers.CharField(max_length=10000)
    recommended_reply = serializers.CharField(max_length=5000)
    public_title = serializers.CharField(max_length=100)
    public_summary = serializers.CharField(max_length=5000)
    input_snapshot = serializers.JSONField()
    factors = StoredFactorSerializer(many=True, min_length=1, max_length=4)

    def validate(self, attrs):
        snapshot = attrs["input_snapshot"]
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("messages"), list):
            raise serializers.ValidationError("판결 입력 스냅샷이 없습니다.")
        if snapshot.get("relation") != attrs["relation"]:
            raise serializers.ValidationError("관계 정보가 일치하지 않습니다.")
        keys = [factor["key"] for factor in attrs["factors"]]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError("판단 요소 키가 중복됩니다.")
        for factor in attrs["factors"]:
            if any(index >= len(snapshot["messages"]) for index in factor["evidence"]):
                raise serializers.ValidationError("대화 근거 번호가 올바르지 않습니다.")
        return attrs

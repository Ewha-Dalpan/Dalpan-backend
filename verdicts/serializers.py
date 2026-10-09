from rest_framework import serializers
from .models import VerdictRequest


class VerdictRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerdictRequest
        fields = ['id', 'case_id', 'stage', 'status', 'progress_step', 'error_message',
                  'created_at', 'started_at', 'finished_at']
        read_only_fields = fields

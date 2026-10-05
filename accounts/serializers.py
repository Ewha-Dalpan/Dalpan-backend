from django.conf import settings
from rest_framework import serializers
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User


class KakaoLoginSerializer(serializers.Serializer):
    code = serializers.CharField()
    redirect_uri = serializers.CharField()

    def validate_redirect_uri(self, value):
        if value not in settings.KAKAO_ALLOWED_REDIRECT_URIS:
            raise serializers.ValidationError("허용되지 않은 redirect_uri입니다.")
        return value


class KakaoSignupSerializer(serializers.Serializer):
    signup_token = serializers.CharField()
    terms = serializers.BooleanField()
    privacy = serializers.BooleanField()
    age_14 = serializers.BooleanField()

    def validate(self, attrs):
        for field in ("terms", "privacy", "age_14"):
            if attrs[field] is not True:
                raise serializers.ValidationError({field: "필수 동의 항목입니다."})
        return attrs


class ActiveTokenRefreshSerializer(TokenRefreshSerializer):
    """재발급 시 정지/탈퇴 계정이면 거부 (simplejwt 버전과 무관하게 직접 검사)"""

    def validate(self, attrs):
        refresh = RefreshToken(attrs["refresh"])  # 무효/만료/블랙리스트면 TokenError → 뷰가 401 처리
        user_id = refresh.payload.get(api_settings.USER_ID_CLAIM)
        if not User.objects.filter(pk=user_id, is_active=True).exists():
            raise AuthenticationFailed("이용할 수 없는 계정입니다.")
        return super().validate(attrs)
    
class WithdrawSerializer(serializers.Serializer):
    confirm = serializers.BooleanField()

    def validate_confirm(self, value):
        if value is not True:
            raise serializers.ValidationError("남은 코인이 소멸됨을 확인해야 탈퇴할 수 있습니다.")
        return value
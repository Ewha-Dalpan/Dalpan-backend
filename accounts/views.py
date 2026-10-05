from django.shortcuts import render
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from coins.models import CoinWallet

from .kakao import KakaoError, fetch_kakao_id
from .models import LoginType, User
from .serializers import ActiveTokenRefreshSerializer, KakaoLoginSerializer, KakaoSignupSerializer, WithdrawSerializer
from .services import (
    InvalidSignupToken, SignupBlocked, is_signup_blocked, issue_tokens,
    make_signup_token, read_signup_token, register_kakao_user,
)
from .withdrawal import withdraw_user


class PublicAPIView(APIView):
    # 만료된 Authorization 헤더가 붙어 와도 401이 나지 않도록 인증 자체를 끈다
    authentication_classes = []
    permission_classes = [AllowAny]


def _login_response(user, status_code=200):
    return Response(
        {"status": "LOGIN", **issue_tokens(user), "user": {"nickname": user.nickname}},
        status=status_code,
    )


class KakaoLoginView(PublicAPIView):
    def post(self, request):
        serializer = KakaoLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            kakao_id = fetch_kakao_id(**serializer.validated_data)
        except KakaoError as e:
            return Response({"detail": str(e)}, status=400 if e.client_error else 502)

        user = User.objects.filter(kakao_id=kakao_id).first()
        if user:
            if not user.is_active:
                return Response({"detail": "이용이 정지된 계정입니다.", "code": "ACCOUNT_SUSPENDED"}, status=403)
            return _login_response(user)

        # 신규 회원: 약관 동의를 받아야 가입할 수 있으므로 가입 토큰만 내려준다
        if is_signup_blocked(LoginType.KAKAO, kakao_id):
            return Response({"detail": "가입할 수 없는 계정입니다.", "code": "SIGNUP_BLOCKED"}, status=403)
        return Response({"status": "NEED_CONSENT", "signup_token": make_signup_token(kakao_id)})


class KakaoSignupView(PublicAPIView):
    def post(self, request):
        serializer = KakaoSignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            kakao_id = read_signup_token(serializer.validated_data["signup_token"])
        except InvalidSignupToken:
            return Response(
                {"detail": "가입 시간이 만료되었습니다. 카카오 로그인을 다시 진행해주세요.",
                 "code": "SIGNUP_TOKEN_EXPIRED"},
                status=400,
            )

        try:
            user, created = register_kakao_user(kakao_id)
        except SignupBlocked:
            return Response({"detail": "가입할 수 없는 계정입니다.", "code": "SIGNUP_BLOCKED"}, status=403)

        if not user.is_active:
            return Response({"detail": "이용이 정지된 계정입니다.", "code": "ACCOUNT_SUSPENDED"}, status=403)
        return _login_response(user, status_code=201 if created else 200)


class ActiveTokenRefreshView(TokenRefreshView):
    serializer_class = ActiveTokenRefreshSerializer


class LogoutView(PublicAPIView):
    def post(self, request):
        refresh = request.data.get("refresh")
        if not refresh:
            return Response({"detail": "refresh 토큰이 필요합니다."}, status=400)
        try:
            RefreshToken(refresh).blacklist()
        except TokenError:
            pass  # 이미 만료/무효인 토큰이면 로그아웃 목적은 달성된 상태
        return Response(status=204)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        balance = CoinWallet.objects.filter(user=user).values_list("balance", flat=True).first() or 0
        return Response({"nickname": user.nickname, "joined_at": user.created_at, "coin_balance": balance})
    
class WithdrawView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = WithdrawSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if request.user.is_staff:
            return Response({"detail": "관리자 계정은 탈퇴할 수 없습니다."}, status=403)
        withdraw_user(request.user.pk)
        return Response(status=204)
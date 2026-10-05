from django.urls import path

from .views import ActiveTokenRefreshView, KakaoLoginView, KakaoSignupView, LogoutView, MeView, WithdrawView

urlpatterns = [
    path("kakao/login/", KakaoLoginView.as_view()),
    path("kakao/signup/", KakaoSignupView.as_view()),
    path("token/refresh/", ActiveTokenRefreshView.as_view()),
    path("logout/", LogoutView.as_view()),
    path("me/", MeView.as_view()),
    path("withdraw/", WithdrawView.as_view()),
]
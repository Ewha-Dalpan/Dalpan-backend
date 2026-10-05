from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from .kakao import unlink_kakao_user
from .models import LoginType, User, UserStatus, WithdrawnIdentity

WITHDRAWN_NICKNAME = "탈퇴한 사용자"


def withdraw_user(user_id: int, *, ban: bool = False) -> bool:
    """
    회원 탈퇴(비식별화). 사용자 행은 지우지 않아 사건·댓글·결제·코인 내역의 연결이 유지된다.
    이미 탈퇴한 계정이면 아무것도 하지 않고 False를 반환한다.
    """
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user_id)
        if user.status == UserStatus.DELETED:
            return False

        kakao_id = user.kakao_id  # 아래에서 NULL로 바꾸기 전에 보관
        raw_identity = kakao_id if user.login_type == LoginType.KAKAO else user.username
        now = timezone.now()

        # 1) 탈퇴 이력: 사람당 해시 하나. 재가입 후 재탈퇴해도 오류가 나지 않게 갱신한다
        identity, created = WithdrawnIdentity.objects.get_or_create(
            identity_hash=WithdrawnIdentity.make_hash(user.login_type, raw_identity),
            defaults={"login_type": user.login_type, "is_banned": ban, "withdrawn_at": now},
        )
        if not created:
            identity.withdrawn_at = now
            identity.is_banned = identity.is_banned or ban
            identity.save(update_fields=["withdrawn_at", "is_banned"])

        # 2) 비식별화
        user.kakao_id = None
        user.username = f"deleted_{user.pk}"
        user.set_unusable_password()
        user.email = None
        user.nickname = WITHDRAWN_NICKNAME
        user.status = UserStatus.DELETED
        user.is_active = False
        user.withdrawn_at = now
        user.save()

        # 3) 발급된 refresh 토큰 전부 무효화
        BlacklistedToken.objects.bulk_create(
            [BlacklistedToken(token_id=pk)
             for pk in OutstandingToken.objects.filter(user_id=user.pk).values_list("pk", flat=True)],
            ignore_conflicts=True,
        )

        # 4) 카카오 연결 끊기는 DB 확정 뒤에 호출 (실패해도 탈퇴는 이미 완료)
        if kakao_id:
            transaction.on_commit(lambda: unlink_kakao_user(kakao_id))
    return True
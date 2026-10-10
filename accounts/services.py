import random

from django.conf import settings
from django.contrib.auth.models import update_last_login
from django.core import signing
from django.db import IntegrityError, transaction
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from uuid import uuid4

from coins.models import CoinTxType, CoinWallet
from coins.services import change_coins

from .models import ConsentType, LoginType, User, UserConsent, WithdrawnIdentity

SIGNUP_BONUS_COINS = 1
SIGNUP_TOKEN_SALT = "accounts.kakao-signup"
SIGNUP_TOKEN_MAX_AGE = 60 * 10  # 10분

ADJECTIVES = [
    "포근한", "따뜻한", "용감한", "수줍은", "느긋한", "엉뚱한", "명랑한", "든든한",
    "상냥한", "씩씩한", "느린", "졸린", "배고픈", "신나는", "차분한", "당당한",
    "똘똘한", "의젓한", "활기찬", "다정한",
]


class SignupBlocked(Exception):
    """제재 중 탈퇴 이력이 있어 재가입이 막힌 경우"""


class InvalidSignupToken(Exception):
    """가입 토큰이 위조되었거나 만료됨"""


def generate_nickname() -> str:
    """[형용사]달토끼[번호]. 이미 쓰는 닉네임이면 번호를 다시 뽑는다 (DB unique는 없음)."""
    nickname = ""
    for attempt in range(10):
        digits = 4 if attempt < 5 else 6
        number = random.randint(10 ** (digits - 1), 10 ** digits - 1)
        nickname = f"{random.choice(ADJECTIVES)}달토끼{number}"
        if not User.objects.filter(nickname=nickname).exists():
            break
    return nickname


def get_withdrawn_identity(login_type, raw_identity):
    return WithdrawnIdentity.objects.filter(
        identity_hash=WithdrawnIdentity.make_hash(login_type, raw_identity)
    ).first()


def is_signup_blocked(login_type, raw_identity) -> bool:
    withdrawn = get_withdrawn_identity(login_type, raw_identity)
    return bool(withdrawn and withdrawn.is_banned)


def make_signup_token(kakao_id: str) -> str:
    return signing.dumps({"kakao_id": kakao_id}, salt=SIGNUP_TOKEN_SALT)


def read_signup_token(token: str) -> str:
    try:
        return signing.loads(token, salt=SIGNUP_TOKEN_SALT, max_age=SIGNUP_TOKEN_MAX_AGE)["kakao_id"]
    except (signing.BadSignature, KeyError):  # SignatureExpired도 BadSignature의 하위 클래스
        raise InvalidSignupToken()


def register_kakao_user(kakao_id: str):
    """신규 카카오 회원 가입. 반환: (user, created)"""
    withdrawn = get_withdrawn_identity(LoginType.KAKAO, kakao_id)
    if withdrawn and withdrawn.is_banned:
        raise SignupBlocked()

    try:
        with transaction.atomic():
            user = User.objects.create_user(
                username=f"kakao_{uuid4().hex}",  # 일반 아이디(밑줄 불가)와 겹치지 않고 카카오 ID도 노출 안 함
                login_type=LoginType.KAKAO,
                kakao_id=kakao_id,
                nickname=generate_nickname(),
            )
            UserConsent.objects.bulk_create([
                UserConsent(user=user, consent_type=ctype, version=settings.TERMS_VERSION)
                for ctype in (ConsentType.TERMS, ConsentType.PRIVACY, ConsentType.AGE_14)
            ])
            CoinWallet.objects.create(user=user)
            if withdrawn is None:  # 탈퇴 이력이 있으면 가입 보너스 없음
                change_coins(
                    user=user, tx_type=CoinTxType.SIGNUP_BONUS, amount=SIGNUP_BONUS_COINS,
                    idempotency_key=f"signup-bonus:{user.pk}",
                )
    except IntegrityError:
        # 동시 요청으로 같은 카카오 회원이 먼저 가입된 경우: 롤백 후 기존 회원으로 처리
        user = User.objects.filter(kakao_id=kakao_id).first()
        if user is None:
            raise
        return user, False
    return user, True


def issue_tokens(user) -> dict:
    refresh = RefreshToken.for_user(user)
    update_last_login(None, user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}

def revoke_all_tokens(user_id: int) -> int:
    """회원의 발급된 refresh 토큰을 전부 무효화한다 (정지 처리 등)."""
    ids = list(OutstandingToken.objects.filter(user_id=user_id).values_list("pk", flat=True))
    BlacklistedToken.objects.bulk_create([BlacklistedToken(token_id=pk) for pk in ids], ignore_conflicts=True)
    return len(ids)
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
ME_URL = "https://kapi.kakao.com/v2/user/me"
TIMEOUT = (3.05, 5)


class KakaoError(Exception):
    """client_error=True: 인가 코드/redirect_uri 문제(400), False: 카카오 통신·설정 문제(502)"""

    def __init__(self, message, *, client_error):
        super().__init__(message)
        self.client_error = client_error


def _fail(res, message):
    try:
        body = res.json()
    except ValueError:
        body = {}
    # 인가 코드가 담길 수 있는 error_description은 로그에 남기지 않는다
    logger.warning("카카오 API 실패 status=%s error=%s error_code=%s",
                   res.status_code, body.get("error"), body.get("error_code"))
    raise KakaoError(message, client_error=(res.status_code == 400))


def fetch_kakao_id(code: str, redirect_uri: str) -> str:
    """인가 코드로 카카오 회원번호를 확인한다. 카카오 토큰은 저장하지 않는다."""
    data = {
        "grant_type": "authorization_code",
        "client_id": settings.KAKAO_REST_API_KEY,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    if settings.KAKAO_CLIENT_SECRET:
        data["client_secret"] = settings.KAKAO_CLIENT_SECRET

    try:
        token_res = requests.post(TOKEN_URL, data=data, timeout=TIMEOUT)
        if token_res.status_code != 200:
            _fail(token_res, "카카오 로그인에 실패했습니다. 다시 시도해주세요.")
        access_token = token_res.json()["access_token"]

        me_res = requests.get(ME_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=TIMEOUT)
        if me_res.status_code != 200:
            _fail(me_res, "카카오 사용자 정보를 확인할 수 없습니다.")
        kakao_id = me_res.json()["id"]
    except (requests.RequestException, ValueError, KeyError):
        logger.exception("카카오 통신/응답 처리 오류")
        raise KakaoError("카카오 서버와 통신할 수 없습니다.", client_error=False)

    return str(kakao_id)
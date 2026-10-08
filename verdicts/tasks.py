from django.conf import settings
from django.utils.module_loading import import_string
from rest_framework.exceptions import APIException


class AIUnavailable(APIException):
    status_code = 503
    default_detail = 'AI 작업 연결이 아직 준비되지 않았습니다.'


def get_dispatcher(stage):
    path = getattr(settings, f'VERDICT_{stage}_DISPATCHER', None)
    if not path:
        raise AIUnavailable()
    return import_string(path)


def dispatch_after_commit(dispatcher, request_id):
    from .services import fail_request
    try:
        dispatcher(request_id)
    except Exception:
        fail_request(request_id, 'AI 작업을 시작하지 못했습니다.')

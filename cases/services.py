import logging

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from coins.services import InsufficientCoins, spend_for_verdict
from verdicts.models import VerdictRequest
from verdicts.tasks import get_dispatcher, dispatch_after_commit

from .models import Case, CaseImage, CaseSituation


def owned_case(user, case_id, *, lock=False):
    queryset = Case.objects.filter(user=user, deleted_at__isnull=True)
    if lock:
        queryset = queryset.select_for_update()
    return get_object_or_404(queryset, pk=case_id)


def create_case(*, user, relation, images):
    """톨 차감과 사건·관계·사진 저장을 함께 확정하고 분석을 요청한다."""
    images = list(images)
    if not 1 <= len(images) <= 6:
        raise ValidationError('대화 이미지를 1~6장 첨부해주세요.')
    dispatcher = get_dispatcher('ANALYSIS')
    saved_files = []
    try:
        with transaction.atomic():
            case = Case.objects.create(user=user, status=Case.Status.CONFIRMING)
            req = VerdictRequest.objects.create(
                case=case, user=user, stage='ANALYSIS', started_at=timezone.now(),
            )
            # 차감 실패 시 사건/요청도 롤백하며 사진은 아직 저장하지 않는다.
            _spend(user, req)
            CaseSituation.objects.create(case=case, relation=relation, analysis_status='RUNNING')
            for index, image in enumerate(images):
                case_image = CaseImage(case=case, sort_order=index)
                case_image.image_key.save(image.name, image, save=False)
                saved_files.append((case_image.image_key.storage, case_image.image_key.name))
                case_image.save()
            # DB 저장과 차감이 확정된 뒤 AI 작업을 등록한다.
            transaction.on_commit(lambda: dispatch_after_commit(dispatcher, req.pk))
        return req
    except Exception:
        # 파일 저장소는 DB 롤백에 포함되지 않으므로 별도로 정리한다.
        for storage, name in saved_files:
            try:
                storage.delete(name)
            except Exception:
                logging.getLogger(__name__).exception('Failed to clean up case upload: %s', name)
        raise


def _spend(user, req):
    try:
        spend_for_verdict(user=user, verdict_request=req)
    except InsufficientCoins:
        raise ValidationError({'balance': '사건 접수에는 1톨이 필요합니다.'})


@transaction.atomic
def submit_case(*, user, case_id):
    case = owned_case(user, case_id, lock=True)
    existing = case.verdict_requests.filter(
        status__in=['PENDING', 'RUNNING', 'AWAITING_CONFIRMATION'],
    ).order_by('-id').first()
    if existing:
        return existing
    if case.status != Case.Status.WRITING:
        raise ValidationError('작성 중인 사건만 접수할 수 있습니다.')
    if not 1 <= case.images.count() <= 6:
        raise ValidationError('대화 이미지를 1~6장 첨부해주세요.')
    dispatcher = get_dispatcher('ANALYSIS')
    situation = case.situations.order_by('-id').first()
    if situation is None:
        raise ValidationError('상황 정보가 없습니다.')
    req = VerdictRequest.objects.create(
        case=case, user=user, stage='ANALYSIS', started_at=timezone.now(),
    )
    _spend(user, req)
    situation.analysis_status = 'RUNNING'
    situation.save(update_fields=['analysis_status', 'updated_at'])
    case.status = Case.Status.CONFIRMING
    case.save(update_fields=['status', 'updated_at'])
    transaction.on_commit(lambda: dispatch_after_commit(dispatcher, req.pk))
    return req


@transaction.atomic
def update_situation(*, user, case_id, changes):
    case = owned_case(user, case_id, lock=True)
    if case.status not in [Case.Status.CONFIRMING, Case.Status.READY]:
        raise ValidationError('상황 확인 단계에서만 수정할 수 있습니다.')
    situation = case.situations.order_by('-id').first()
    if situation is None or situation.analysis_status != 'DONE':
        raise ValidationError('AI 상황 분석이 아직 완료되지 않았습니다.')
    if set(changes) - {'summary', 'user_speaker_side'}:
        raise ValidationError('화자 위치와 상황만 수정할 수 있습니다.')
    situation.confirmed_at = None
    for key, value in changes.items():
        setattr(situation, key, value)
    situation.full_clean()
    situation.save()
    case.save(update_fields=['updated_at'])
    return case


@transaction.atomic
def request_judgment(*, user, case_id):
    case = owned_case(user, case_id, lock=True)
    active = case.verdict_requests.filter(stage='JUDGMENT', status__in=['PENDING', 'RUNNING']).first()
    if active:
        return active
    if case.status not in [Case.Status.CONFIRMING, Case.Status.READY]:
        raise ValidationError('상황 확인이 끝난 사건만 판결받을 수 있습니다.')
    situation = case.situations.order_by('-id').first()
    if situation is None or situation.analysis_status != 'DONE' or not case.issues.exists():
        raise ValidationError('AI 상황 분석이 아직 완료되지 않았습니다.')
    dispatcher = get_dispatcher('JUDGMENT')
    req = case.verdict_requests.select_for_update().filter(status='AWAITING_CONFIRMATION').first()
    if req is None:
        req = VerdictRequest.objects.create(case=case, user=user, stage='JUDGMENT')
        _spend(user, req)
    req.stage = 'JUDGMENT'
    req.status = 'PENDING'
    req.progress_step = 'PLANNING_FACTORS'
    req.started_at = timezone.now()
    req.save(update_fields=['stage', 'status', 'progress_step', 'started_at'])
    situation.confirmed_at = timezone.now()
    situation.save(update_fields=['confirmed_at', 'updated_at'])
    case.status = Case.Status.JUDGING
    case.save(update_fields=['status', 'updated_at'])
    transaction.on_commit(lambda: dispatch_after_commit(dispatcher, req.pk))
    return req

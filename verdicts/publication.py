from uuid import uuid4
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from cases.models import Case
from .models import Verdict, VerdictShare

def available_cases():
    return Case.objects.filter(status="JUDGED", is_hidden=False, deleted_at__isnull=True)

def latest_verdict(case):
    verdict = Verdict.objects.filter(case=case, request__status="DONE").order_by("-id").first()
    if not verdict or not verdict.public_title or not verdict.public_summary:
        raise ValidationError("공개용 판결 결과가 없습니다. 새 판결이 필요합니다.")
    return verdict

@transaction.atomic


def create_share(user, case_id):
    case = get_object_or_404(available_cases().select_for_update(), pk=case_id, user=user)
    verdict = latest_verdict(case)
    share, created = VerdictShare.objects.get_or_create(verdict=verdict)
    if not created and not share.is_active:
        share.token = uuid4()
        share.is_active = True
        share.save(update_fields=["token", "is_active"])
    return share

@transaction.atomic


def revoke_share(user, case_id):
    case = get_object_or_404(Case.objects.select_for_update(), pk=case_id, user=user)
    VerdictShare.objects.filter(verdict__case=case).update(is_active=False)

@transaction.atomic


def publish_case(user, case_id):
    case = get_object_or_404(available_cases().select_for_update(), pk=case_id, user=user)
    verdict = latest_verdict(case)
    if not case.is_public:
        case.is_public = True
        case.public_at = timezone.now()
        case.save(update_fields=["is_public", "public_at", "updated_at"])
    return case, verdict

@transaction.atomic


def unpublish_case(user, case_id):
    case = get_object_or_404(Case.objects.select_for_update(), pk=case_id, user=user)
    case.is_public = False
    case.public_at = None
    case.save(update_fields=["is_public", "public_at", "updated_at"])

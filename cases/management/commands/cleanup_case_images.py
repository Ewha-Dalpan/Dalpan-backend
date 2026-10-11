from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from cases.models import Case


class Command(BaseCommand):
    help = "보관 기간이 지난 삭제/취소 사건의 이미지 정리 (기본은 미리보기)"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="저장소 파일과 이미지 행 실제 삭제")

    def handle(self, *args, **options):
        days = settings.CASE_IMAGE_RETENTION_DAYS
        if days <= 0:
            self.stdout.write("이미지 자동 삭제가 비활성화되어 있습니다.")
            return
        cutoff = timezone.now() - timedelta(days=days)
        ids = Case.objects.filter(
            Q(deleted_at__lt=cutoff) |
            Q(verdict_requests__status="CANCELED", verdict_requests__finished_at__lt=cutoff)
        ).distinct().values_list("pk", flat=True)
        count = failures = 0
        for case_id in ids.iterator():
            with transaction.atomic():
                case = Case.objects.select_for_update().get(pk=case_id)
                latest = case.verdict_requests.order_by("-id").first()
                if case.verdict_requests.filter(status__in=["PENDING", "RUNNING", "AWAITING_CONFIRMATION"]).exists():
                    continue
                deleted = case.deleted_at is not None and case.deleted_at < cutoff
                canceled = (
                    latest is not None and latest.status == "CANCELED"
                    and latest.finished_at is not None and latest.finished_at < cutoff
                    and case.status != Case.Status.JUDGED
                )
                if not (deleted or canceled):
                    continue
                for image in case.images.all():
                    count += 1
                    if options["apply"]:
                        try:
                            image.image_key.delete(save=False)
                        except Exception:
                            failures += 1
                            self.stderr.write(f"이미지 정리 실패: image_id={image.pk}")
                            continue
                        image.delete()
        self.stdout.write(f"{'삭제' if options['apply'] else '삭제 예정'} 이미지: {count}개")
        if failures:
            raise CommandError(f"이미지 {failures}개 정리 실패. 다음 실행에서 재시도하세요.")

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from cases.models import CaseImage

RETENTION_DAYS = 30  # 탈퇴 후 원본 이미지 보관 기간


class Command(BaseCommand):
    help = "탈퇴 후 30일이 지난 사용자의 사건 원본 이미지 삭제 (기본은 미리보기, 실제 삭제는 --apply)"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="저장소 파일과 이미지 행 실제 삭제")

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=RETENTION_DAYS)
        images = CaseImage.objects.filter(case__user__withdrawn_at__lt=cutoff)
        count = failures = 0
        for image in images.iterator():
            count += 1
            if not options["apply"]:
                continue
            try:
                image.image_key.delete(save=False)  # 저장소(로컬/S3) 파일 먼저 삭제
            except Exception:
                failures += 1
                self.stderr.write(f"이미지 삭제 실패: image_id={image.pk}")
                continue
            image.delete()
        self.stdout.write(f"{'삭제' if options['apply'] else '삭제 예정'} 이미지: {count}개")
        if failures:
            raise CommandError(f"이미지 {failures}개 삭제 실패. 다음 실행에서 재시도하세요.")
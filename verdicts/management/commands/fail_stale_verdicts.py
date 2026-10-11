from django.core.management.base import BaseCommand

from verdicts.services import expire_abandoned, fail_stale_requests


class Command(BaseCommand):
    help = "시간 초과 판결 요청을 실패 처리(코인 반환)하고, 오래 방치된 확인 대기 사건을 만료 처리한다"

    def handle(self, *args, **options):
        failed = fail_stale_requests()
        expired = expire_abandoned()
        self.stdout.write(f"시간 초과 실패 처리: {failed}건 / 방치 만료: {len(expired)}건")
        # 이미지 보관 기간 정리는 cleanup_case_images 명령으로 별도 실행한다.
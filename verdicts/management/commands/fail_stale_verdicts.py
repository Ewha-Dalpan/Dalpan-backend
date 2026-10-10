from django.core.management.base import BaseCommand

from verdicts.services import expire_abandoned, fail_stale_requests


class Command(BaseCommand):
    help = "시간 초과 판결 요청을 실패 처리(코인 반환)하고, 오래 방치된 확인 대기 사건을 만료 처리한다"

    def handle(self, *args, **options):
        failed = fail_stale_requests()
        expired = expire_abandoned()
        self.stdout.write(f"시간 초과 실패 처리: {failed}건 / 방치 만료: {len(expired)}건")
        # expired(사건 ID 목록)의 업로드 이미지 삭제는 이미지 정책 확정 후 여기에 연결
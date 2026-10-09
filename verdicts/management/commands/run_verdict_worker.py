# verdicts/management/commands/run_verdict_worker.py

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from verdicts.models import VerdictRequest
from verdicts.services import fail_stale_requests
from verdicts.tasks import process_request


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "대기 중인 상황 분석과 판결 요청을 순서대로 처리합니다."

    def handle(self, *args, **options):
        if not getattr(settings, "LINER_API_KEY", ""):
            raise CommandError("LINER_API_KEY 설정이 필요합니다.")

        self.stdout.write(
            self.style.SUCCESS(
                "Verdict 워커 시작. 현재 SQLite 환경에서는 하나만 실행하세요."
            )
        )

        try:
            while True:
                close_old_connections()

                try:
                    # 워커가 중단되었던 요청도 제한 시간이 지나면 실패·환불한다.
                    fail_stale_requests()

                    request_id = (
                        VerdictRequest.objects.filter(status="PENDING")
                        .order_by("created_at", "id")
                        .values_list("id", flat=True)
                        .first()
                    )

                    if request_id is None:
                        time.sleep(2)
                        continue

                    self.stdout.write(f"요청 {request_id} 처리 시작")
                    process_request(request_id)
                    self.stdout.write(f"요청 {request_id} 처리 종료")

                except Exception as exc:
                    logger.error(
                        "Verdict worker loop error: error_type=%s",
                        type(exc).__name__,
                    )
                    self.stderr.write(
                        "작업 처리 오류가 발생했습니다. 2초 후 다시 확인합니다."
                    )
                    time.sleep(2)

                finally:
                    close_old_connections()

        except KeyboardInterrupt:
            self.stdout.write("Verdict 워커를 종료합니다.")
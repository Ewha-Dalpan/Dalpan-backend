# verdicts/management/commands/fail_stale_verdicts.py
from django.core.management.base import BaseCommand

from verdicts.services import fail_stale_requests


class Command(BaseCommand):
    help = "제한 시간(기본 10분) 넘게 끝나지 않은 판결 요청을 실패 처리하고 코인을 반환한다"

    def handle(self, *args, **options):
        self.stdout.write(f"실패 처리: {fail_stale_requests()}건")
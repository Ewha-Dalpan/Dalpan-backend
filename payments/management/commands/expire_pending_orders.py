from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from payments.models import OrderStatus, PaymentOrder


class Command(BaseCommand):
    help = "오래 결제되지 않은 PENDING 주문을 EXPIRED로 바꾼다 (만료 후 결제가 확인돼도 웹훅이 지급한다)"

    def handle(self, *args, **options):
        hours = getattr(settings, "PAYMENT_ORDER_EXPIRE_HOURS", 24)
        cutoff = timezone.now() - timedelta(hours=hours)
        count = PaymentOrder.objects.filter(
            status=OrderStatus.PENDING, created_at__lt=cutoff
        ).update(status=OrderStatus.EXPIRED, updated_at=timezone.now())
        self.stdout.write(f"만료 처리: {count}건")
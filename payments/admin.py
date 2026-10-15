from django.contrib import admin

from .models import CoinProduct, PaddleWebhookEvent, PaymentOrder, PaymentRefund


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CoinProduct)
class CoinProductAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "coin_amount", "price_krw", "paddle_price_id", "is_active", "sort_order")
    list_filter = ("is_active",)
    ordering = ("sort_order", "id")

    def has_delete_permission(self, request, obj=None):
        return False  # 주문이 참조하므로 삭제 대신 판매 중지(is_active)로 처리


@admin.register(PaymentOrder)
class PaymentOrderAdmin(ReadOnlyAdmin):
    list_display = ("id", "user", "product", "coin_amount", "price_krw", "status", "paid_at", "created_at")
    list_filter = ("status",)
    search_fields = ("user__nickname", "paddle_transaction_id")
    ordering = ("-created_at",)


@admin.register(PaymentRefund)
class PaymentRefundAdmin(ReadOnlyAdmin):
    list_display = ("id", "order", "status", "refund_amount", "coins_revoked", "created_at")
    list_filter = ("status",)


@admin.register(PaddleWebhookEvent)
class PaddleWebhookEventAdmin(ReadOnlyAdmin):
    # '결과 = 데이터 오류' 필터로 돈은 받았는데 지급이 안 된 건을 찾는다
    list_display = ("created_at", "event_type", "result", "note", "order")
    list_filter = ("result", "event_type")
    search_fields = ("event_id", "note")
    ordering = ("-id",)
from uuid import uuid4

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect
from django.urls import reverse

from accounts.models import UserStatus

from .models import CoinLedger, CoinTxType, CoinWallet
from .services import InsufficientCoins, change_coins


class AdminCoinForm(forms.ModelForm):
    tx_type = forms.ChoiceField(
        label="구분",
        choices=[(CoinTxType.ADMIN_GRANT, "지급"), (CoinTxType.ADMIN_DEDUCT, "차감")],
    )
    amount = forms.IntegerField(label="코인 수량 (양수로 입력)", min_value=1)
    memo = forms.CharField(label="사유 (필수)", max_length=255)

    class Meta:
        model = CoinLedger
        fields = ("user", "tx_type", "amount", "memo")

    def clean(self):
        data = super().clean()
        user, kind, amount = data.get("user"), data.get("tx_type"), data.get("amount")
        if user and user.status == UserStatus.DELETED:
            raise ValidationError("탈퇴한 회원에게는 처리할 수 없습니다.")
        if user and kind == CoinTxType.ADMIN_DEDUCT and amount:
            balance = CoinWallet.objects.filter(user=user).values_list("balance", flat=True).first() or 0
            if balance < amount:
                raise ValidationError(f"현재 잔액({balance})보다 많이 차감할 수 없습니다.")
        return data


@admin.register(CoinLedger)
class CoinLedgerAdmin(admin.ModelAdmin):
    form = AdminCoinForm
    list_display = ("id", "user", "tx_type", "amount", "balance_after", "case", "created_at")
    list_filter = ("tx_type",)
    search_fields = ("user__nickname", "idempotency_key")
    autocomplete_fields = ("user",)
    ordering = ("-id",)

    def has_change_permission(self, request, obj=None):
        return False  # 내역은 수정 불가

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        amount = form.cleaned_data["amount"]
        signed = amount if obj.tx_type == CoinTxType.ADMIN_GRANT else -amount
        try:
            ledger, _ = change_coins(
                user=obj.user, tx_type=obj.tx_type, amount=signed,
                idempotency_key=f"admin:{uuid4().hex}", memo=obj.memo, created_by=request.user,
            )
        except InsufficientCoins:  # 입력 검증 직후 잔액이 바뀐 드문 경우
            messages.error(request, "잔액이 부족해 차감하지 못했습니다.")
            obj.pk = None
            return
        obj.pk = ledger.pk

    def log_addition(self, request, obj, message):
        if obj.pk:
            return super().log_addition(request, obj, message)

    def response_add(self, request, obj, post_url_continue=None):
        if not obj.pk:
            return HttpResponseRedirect(reverse("admin:coins_coinledger_changelist"))
        return super().response_add(request, obj, post_url_continue)


@admin.register(CoinWallet)
class CoinWalletAdmin(admin.ModelAdmin):
    list_display = ("user", "balance", "updated_at")
    search_fields = ("user__nickname",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False  # 잔액은 코인 변경 함수로만 바뀐다

    def has_delete_permission(self, request, obj=None):
        return False
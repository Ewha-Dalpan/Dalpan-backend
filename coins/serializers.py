from rest_framework import serializers

from .models import CoinLedger


class CoinLedgerSerializer(serializers.ModelSerializer):
    tx_type_label = serializers.CharField(source="get_tx_type_display", read_only=True)

    class Meta:
        model = CoinLedger
        fields = ["id", "tx_type", "tx_type_label", "amount", "balance_after", "case", "created_at"]
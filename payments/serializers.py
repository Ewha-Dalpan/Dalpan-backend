import re

from django.conf import settings
from rest_framework import serializers

from .models import CoinProduct


class CoinProductSerializer(serializers.ModelSerializer):
    base_coin_amount = serializers.SerializerMethodField()

    def get_base_coin_amount(self, obj):
        return obj.coin_amount - obj.bonus_coin_amount

    class Meta:
        model = CoinProduct
        fields = ["id", "code", "name", "coin_amount", "base_coin_amount", "bonus_coin_amount", "price_krw"]


class OrderCreateSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()  # 금액은 받지 않는다 (가격은 서버가 정한다)

    recvphone = serializers.CharField(max_length=20)

    def validate_recvphone(self, value):
        value = value.replace("-", "").replace(" ", "")
        if not re.fullmatch(r"010[0-9]{8}", value):
            raise serializers.ValidationError("01012345678 형식으로 입력해 주세요.")
        return value

    def validate(self, attrs):
        product = CoinProduct.objects.filter(pk=attrs["product_id"], is_active=True).first()
        if product is None:
            raise serializers.ValidationError({"product_id": "판매 중인 상품이 아닙니다."})
        if product.price_krw < settings.PAYAPP_MIN_PRICE_KRW:
            raise serializers.ValidationError({"product_id": "최소 결제금액은 1,000원입니다."})
        if product.coin_amount <= 0 or product.bonus_coin_amount > product.coin_amount:
            raise serializers.ValidationError({"product_id": "상품 지급 수량을 확인해야 합니다."})
        attrs["product"] = product
        return attrs
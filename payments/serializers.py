from rest_framework import serializers

from .models import CoinProduct


class CoinProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = CoinProduct
        fields = ["id", "name", "coin_amount", "price_krw"]


class OrderCreateSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()  # 금액은 받지 않는다 (가격은 서버가 정한다)

    def validate(self, attrs):
        product = CoinProduct.objects.filter(pk=attrs["product_id"], is_active=True).first()
        if product is None:
            raise serializers.ValidationError({"product_id": "판매 중인 상품이 아닙니다."})
        attrs["product"] = product
        return attrs
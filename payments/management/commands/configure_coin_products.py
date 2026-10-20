from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from payments.models import CoinProduct


class Command(BaseCommand):
    help = "Configure the 5/10/20/50 coin catalog using real Paddle price IDs."

    def add_arguments(self, parser):
        for amount in (5, 10, 20, 50):
            parser.add_argument(f"--price-{amount}", required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        amounts = (5, 10, 20, 50)
        ids = [options[f"price_{amount}"] for amount in amounts]
        if any(not value.startswith("pri_") for value in ids) or len(set(ids)) != 4:
            raise CommandError("Provide four distinct Paddle price IDs (pri_...).")
        selected = []
        for position, (amount, price_id) in enumerate(zip(amounts, ids)):
            # Retain IDs and price references of existing products/orders.
            product = CoinProduct.objects.filter(paddle_price_id=price_id).first()
            if product is None:
                candidates = list(CoinProduct.objects.filter(coin_amount=amount))
                if len(candidates) > 1:
                    raise CommandError(f"Ambiguous existing {amount}-coin products.")
                product = candidates[0] if candidates else CoinProduct(paddle_price_id=price_id)
            if product.pk and (product.coin_amount != amount or product.paddle_price_id != price_id):
                raise CommandError(f"Existing {amount}-coin product has a different Paddle price. Review it first.")
            product.name = f"{amount}\uD1A8"
            product.coin_amount = amount
            product.bonus_coin_amount = 5 if amount == 50 else 0
            product.price_krw = amount * 800
            product.is_active = True
            product.sort_order = position
            product.save()
            selected.append(product.pk)
        CoinProduct.objects.exclude(pk__in=selected).update(is_active=False)
        self.stdout.write(self.style.SUCCESS("Configured 5/10/20/50 coins; 50 coins grants 55. Other products disabled."))

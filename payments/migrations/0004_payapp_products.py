from django.db import migrations


def seed_products(apps, schema_editor):
    Product = apps.get_model("payments", "CoinProduct")
    products = Product.objects.using(schema_editor.connection.alias)
    # Preserve historical products and all order snapshots.
    products.filter(is_active=True).update(is_active=False)
    for position, (code, name, base, bonus, price) in enumerate([
        ("tol-1", "1톨", 1, 0, 1000),
        ("tol-5", "5톨", 5, 0, 4950),
        ("tol-10", "10톨", 10, 0, 9900),
        ("tol-20", "20톨", 20, 0, 19800),
        ("tol-50", "50톨 + 5톨 보너스", 50, 5, 49500),
    ], start=1):
        products.update_or_create(code=code, defaults={
            "name": name, "coin_amount": base + bonus, "bonus_coin_amount": bonus,
            "price_krw": price, "is_active": True, "sort_order": position,
        })


def disable_products(apps, schema_editor):
    apps.get_model("payments", "CoinProduct").objects.using(
        schema_editor.connection.alias).filter(
            code__in=["tol-1", "tol-5", "tol-10", "tol-20", "tol-50"]
        ).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [("payments", "0003_payapp")]
    operations = [migrations.RunPython(seed_products, disable_products)]

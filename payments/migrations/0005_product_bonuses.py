from django.db import migrations


def update_bonuses(apps, schema_editor):
    products = apps.get_model("payments", "CoinProduct").objects.using(
        schema_editor.connection.alias)
    for code, base, bonus in [("tol-10", 10, 1), ("tol-20", 20, 3)]:
        products.filter(code=code).update(
            name=f"{base}톨 + {bonus}톨 보너스",
            coin_amount=base + bonus, bonus_coin_amount=bonus)


def restore_bonuses(apps, schema_editor):
    products = apps.get_model("payments", "CoinProduct").objects.using(
        schema_editor.connection.alias)
    for code, base in [("tol-10", 10), ("tol-20", 20)]:
        products.filter(code=code).update(
            name=f"{base}톨", coin_amount=base, bonus_coin_amount=0)


class Migration(migrations.Migration):
    dependencies = [("payments", "0004_payapp_products")]
    operations = [migrations.RunPython(update_bonuses, restore_bonuses)]

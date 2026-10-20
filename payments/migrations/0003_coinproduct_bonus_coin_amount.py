from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0002_alter_paddlewebhookevent_result")]
    operations = [
        migrations.AddField(
            model_name="coinproduct",
            name="bonus_coin_amount",
            field=models.PositiveIntegerField(default=0),
        ),
    ]

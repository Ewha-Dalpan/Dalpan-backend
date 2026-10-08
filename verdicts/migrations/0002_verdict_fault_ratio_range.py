import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('verdicts', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='verdict',
            name='fault_ratio',
            field=models.PositiveSmallIntegerField(
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(100),
                ],
            ),
        ),
        migrations.AddConstraint(
            model_name='verdict',
            constraint=models.CheckConstraint(
                condition=models.Q(fault_ratio__gte=0, fault_ratio__lte=100),
                name='verdict_fault_ratio_0_100',
            ),
        ),
    ]

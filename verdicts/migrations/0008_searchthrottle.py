from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("verdicts", "0007_verdict_public_one_line_verdictfactor_public_name_and_more")]
    operations = [migrations.CreateModel(
        name="SearchThrottle",
        fields=[
            ("kind", models.CharField(max_length=10, primary_key=True, serialize=False)),
            ("next_at", models.DateTimeField()),
        ],
        options={"db_table": "verdict_search_throttles"},
    )]

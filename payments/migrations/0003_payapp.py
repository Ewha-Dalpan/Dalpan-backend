import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("payments", "0002_alter_paddlewebhookevent_result")]
    operations = [
        migrations.AlterField(model_name="coinproduct", name="paddle_price_id",
                              field=models.CharField(max_length=64, unique=True, null=True, blank=True)),
        migrations.AddField(model_name="coinproduct", name="code",
                            field=models.SlugField(max_length=32, unique=True, null=True, blank=True)),
        migrations.AddField(model_name="coinproduct", name="bonus_coin_amount",
                            field=models.PositiveIntegerField(default=0)),
        migrations.AlterField(model_name="paymentorder", name="status",
                              field=models.CharField(max_length=10, default="PENDING", choices=[
                                  ("PENDING", "결제 대기"), ("PAID", "결제 완료"),
                                  ("EXPIRED", "만료"), ("REFUNDED", "환불"), ("CANCELLED", "결제 요청 취소")])),
        migrations.AddField(model_name="paymentorder", name="provider",
                            field=models.CharField(max_length=10, default="paddle",
                                                   choices=[("paddle", "Paddle"), ("payapp", "PayApp")])),
        migrations.AddField(model_name="paymentorder", name="payapp_mul_no",
                            field=models.CharField(max_length=64, unique=True, null=True, blank=True)),
        migrations.AddField(model_name="paymentorder", name="payapp_pay_url",
                            field=models.URLField(max_length=1000, blank=True)),
        migrations.AddField(model_name="paymentorder", name="payapp_token",
                            field=models.UUIDField(default=uuid.uuid4, editable=False)),
        migrations.AddField(model_name="paymentorder", name="checkout_key",
                            field=models.UUIDField(unique=True, null=True, blank=True)),
        migrations.AddField(model_name="paymentorder", name="payapp_request_status",
                            field=models.CharField(max_length=12, blank=True)),
        migrations.CreateModel(name="PayAppFeedback", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("mul_no", models.CharField(max_length=64, blank=True)),
            ("pay_state", models.CharField(max_length=10, blank=True)),
            ("result", models.CharField(max_length=20, default="PROCESSED", choices=[
                ("PROCESSED", "처리 완료"), ("IGNORED", "무시"), ("DATA_ERROR", "확인 필요")])),
            ("note", models.CharField(max_length=255, blank=True)),
            ("payload", models.JSONField(default=dict)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("order", models.ForeignKey(to="payments.paymentorder", null=True, blank=True,
                                        related_name="payapp_feedbacks", on_delete=django.db.models.deletion.SET_NULL)),
        ], options={"db_table": "payapp_feedbacks"}),
        migrations.CreateModel(name="PayAppRefund", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("refund_amount", models.PositiveIntegerField()),
            ("coins_revoked", models.PositiveIntegerField(default=0)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("order", models.OneToOneField(to="payments.paymentorder", related_name="payapp_refund",
                                          on_delete=django.db.models.deletion.PROTECT)),
        ], options={"db_table": "payapp_refunds"}),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('verdicts', '0002_verdict_fault_ratio_range')]
    operations = [
        migrations.AddField(
            model_name='verdictrequest', name='stage',
            field=models.CharField(max_length=10, default='JUDGMENT',
                                   choices=[('ANALYSIS', '상황 분석'), ('JUDGMENT', '판결 생성')]),
        ),
        migrations.AlterField(
            model_name='verdictrequest', name='status',
            field=models.CharField(max_length=30, default='PENDING', choices=[
                ('PENDING', '처리대기'), ('RUNNING', '처리중'), ('DONE', '완료'),
                ('FAILED', '실패'), ('AWAITING_CONFIRMATION', '상황 확인 대기'),
            ]),
        ),
    ]

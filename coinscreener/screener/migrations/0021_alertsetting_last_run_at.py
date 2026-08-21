from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('screener', '0020_strategy_owner_key'),
    ]

    operations = [
        migrations.AddField(
            model_name='alertsetting',
            name='last_run_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='마지막 예약 실행 시각'),
        ),
    ]

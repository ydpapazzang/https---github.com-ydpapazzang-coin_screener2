from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('screener', '0002_alter_condition_indicators'),
    ]

    operations = [
        migrations.CreateModel(
            name='AlertSetting',
            fields=[
                ('id',          models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('enabled',     models.BooleanField(default=False, verbose_name='자동 알림 활성화')),
                ('alert_hour',  models.IntegerField(default=9,  verbose_name='알림 시각 (시)')),
                ('alert_min',   models.IntegerField(default=0,  verbose_name='알림 시각 (분)')),
                ('exchange',    models.CharField(default='upbit', max_length=20)),
                ('vol_limit',   models.IntegerField(default=100)),
                ('updated_at',  models.DateTimeField(auto_now=True)),
                ('strategy',    models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='alert',
                    to='screener.strategy',
                )),
            ],
        ),
    ]

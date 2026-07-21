# Generated manually for CustomAlert (알림 탭 커스텀 알림)

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('screener', '0015_add_change_rate_indicator'),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomAlert',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('exchange', models.CharField(default='upbit', max_length=20)),
                ('interval_min', models.IntegerField(default=15, verbose_name='자동검색 주기(분)')),
                ('dedup_min', models.IntegerField(default=60, verbose_name='중복방지(분)')),
                ('enabled', models.BooleanField(default=True)),
                ('last_run_at', models.DateTimeField(blank=True, null=True, verbose_name='마지막 실행 시각')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('strategy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='custom_alerts', to='screener.strategy')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]

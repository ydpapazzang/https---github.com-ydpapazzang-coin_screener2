from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('screener', '0022_dailyrecommendation_trade_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailyrecommendation',
            name='entered_at',
            field=models.DateTimeField(
                blank=True, null=True, verbose_name='실제 진입 시각'
            ),
        ),
        migrations.AddField(
            model_name='dailyrecommendation',
            name='entry_expires_on',
            field=models.DateField(
                blank=True, null=True, verbose_name='진입 신호 만료일'
            ),
        ),
        migrations.AddField(
            model_name='dailyrecommendation',
            name='exit_at',
            field=models.DateTimeField(
                blank=True, null=True, verbose_name='최종 청산 시각'
            ),
        ),
        migrations.AddField(
            model_name='dailyrecommendation',
            name='exit_price',
            field=models.FloatField(
                blank=True, null=True, verbose_name='최종 청산가'
            ),
        ),
        migrations.AddField(
            model_name='dailyrecommendation',
            name='exit_reason',
            field=models.CharField(
                blank=True, max_length=30, verbose_name='청산 사유'
            ),
        ),
        migrations.AddField(
            model_name='dailyrecommendation',
            name='initial_stop_loss',
            field=models.FloatField(
                blank=True, null=True, verbose_name='초기 손절가'
            ),
        ),
        migrations.AddField(
            model_name='dailyrecommendation',
            name='partial_exit_at',
            field=models.DateTimeField(
                blank=True, null=True, verbose_name='부분 익절 시각'
            ),
        ),
        migrations.AddField(
            model_name='dailyrecommendation',
            name='partial_exit_price',
            field=models.FloatField(
                blank=True, null=True, verbose_name='부분 익절가'
            ),
        ),
        migrations.AlterField(
            model_name='dailyrecommendation',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', '진입대기'),
                    ('active', '매수완료'),
                    ('success', '목표달성'),
                    ('partial', '부분익절'),
                    ('failed', '손절이탈'),
                    ('closed', '마감'),
                    ('skipped', '추천휴식'),
                ],
                default='pending',
                max_length=20,
                verbose_name='상태',
            ),
        ),
    ]

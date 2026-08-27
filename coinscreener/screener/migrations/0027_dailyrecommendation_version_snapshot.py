from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('screener', '0026_scanusage'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailyrecommendation',
            name='strategy_version',
            field=models.CharField(blank=True, default='', max_length=80, verbose_name='전략 버전'),
        ),
        migrations.AddField(
            model_name='dailyrecommendation',
            name='strategy_parameters',
            field=models.JSONField(blank=True, default=dict, verbose_name='전략 파라미터'),
        ),
        migrations.AddField(
            model_name='dailyrecommendation',
            name='market_regime',
            field=models.JSONField(blank=True, default=dict, verbose_name='생성 당시 시장 상태'),
        ),
        migrations.AddField(
            model_name='dailyrecommendation',
            name='data_as_of',
            field=models.DateTimeField(blank=True, null=True, verbose_name='추천 데이터 기준 시각'),
        ),
        migrations.AddField(
            model_name='dailyrecommendation',
            name='code_version',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='코드 버전'),
        ),
    ]


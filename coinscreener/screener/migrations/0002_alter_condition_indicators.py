from django.db import migrations, models

INDICATOR_CHOICES = [
    ('MA',        '단순이동평균(SMA)'),
    ('EMA',       '지수이동평균(EMA)'),
    ('WMA',       '가중이동평균(WMA)'),
    ('RSI',       'RSI'),
    ('BB_UPPER',  '볼린저 상단'),
    ('BB_MIDDLE', '볼린저 중단'),
    ('BB_LOWER',  '볼린저 하단'),
    ('VAL',       '고정값'),
    ('CLOSE',     '종가'),
]

class Migration(migrations.Migration):

    dependencies = [
        ('screener', '0001_initial'),
    ]

    operations = [
        # ① indicator 컬럼 choices + max_length 확장
        migrations.AlterField(
            model_name='condition',
            name='left_indicator',
            field=models.CharField(max_length=15, choices=INDICATOR_CHOICES, default='MA'),
        ),
        migrations.AlterField(
            model_name='condition',
            name='right_indicator',
            field=models.CharField(max_length=15, choices=INDICATOR_CHOICES, default='MA'),
        ),
        # ② DB에 이미 있는 bb 컬럼을 nullable로 모델에 추가
        #    (DB에 컬럼이 이미 있으면 이 operation은 무시되므로 안전)
        migrations.AddField(
            model_name='condition',
            name='bb_period',
            field=models.IntegerField(null=True, blank=True, verbose_name='볼린저 기간(미사용)'),
        ),
        migrations.AddField(
            model_name='condition',
            name='bb_std',
            field=models.FloatField(null=True, blank=True, verbose_name='볼린저 표준편차(미사용)'),
        ),
    ]
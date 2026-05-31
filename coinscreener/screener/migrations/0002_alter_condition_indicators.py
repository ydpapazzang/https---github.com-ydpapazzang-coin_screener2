from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('screener', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='condition',
            name='left_indicator',
            field=models.CharField(
                max_length=15,
                choices=[
                    ('MA',  '단순이동평균(SMA)'),
                    ('EMA', '지수이동평균(EMA)'),
                    ('WMA', '가중이동평균(WMA)'),
                    ('RSI', 'RSI'),
                    ('BB_UPPER',  '볼린저 상단'),
                    ('BB_MIDDLE', '볼린저 중단'),
                    ('BB_LOWER',  '볼린저 하단'),
                    ('VAL',   '고정값'),
                    ('CLOSE', '종가'),
                ],
                default='MA',
            ),
        ),
        migrations.AlterField(
            model_name='condition',
            name='right_indicator',
            field=models.CharField(
                max_length=15,
                choices=[
                    ('MA',  '단순이동평균(SMA)'),
                    ('EMA', '지수이동평균(EMA)'),
                    ('WMA', '가중이동평균(WMA)'),
                    ('RSI', 'RSI'),
                    ('BB_UPPER',  '볼린저 상단'),
                    ('BB_MIDDLE', '볼린저 중단'),
                    ('BB_LOWER',  '볼린저 하단'),
                    ('VAL',   '고정값'),
                    ('CLOSE', '종가'),
                ],
                default='MA',
            ),
        ),
    ]

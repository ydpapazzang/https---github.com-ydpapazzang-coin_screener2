from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('screener', '0003_alertsetting'),
    ]

    operations = [
        # left_indicator / right_indicator: max_length 확장 + HA 선택지 추가
        migrations.AlterField(
            model_name='condition',
            name='left_indicator',
            field=models.CharField(
                max_length=15,
                choices=[
                    ('MA','단순이동평균(SMA)'),('EMA','지수이동평균(EMA)'),('WMA','가중이동평균(WMA)'),
                    ('RSI','RSI'),('BB_UPPER','볼린저 상단'),('BB_MIDDLE','볼린저 중단'),('BB_LOWER','볼린저 하단'),
                    ('HA_BULL','HA 양봉'),('HA_BEAR','HA 음봉'),
                    ('HA_BULL_N','HA 연속 양봉'),('HA_BEAR_N','HA 연속 음봉'),
                    ('HA_NO_LOWER','HA 아랫꼬리 없음'),('HA_NO_UPPER','HA 윗꼬리 없음'),
                    ('VAL','고정값'),('CLOSE','종가'),
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
                    ('MA','단순이동평균(SMA)'),('EMA','지수이동평균(EMA)'),('WMA','가중이동평균(WMA)'),
                    ('RSI','RSI'),('BB_UPPER','볼린저 상단'),('BB_MIDDLE','볼린저 중단'),('BB_LOWER','볼린저 하단'),
                    ('HA_BULL','HA 양봉'),('HA_BEAR','HA 음봉'),
                    ('HA_BULL_N','HA 연속 양봉'),('HA_BEAR_N','HA 연속 음봉'),
                    ('HA_NO_LOWER','HA 아랫꼬리 없음'),('HA_NO_UPPER','HA 윗꼬리 없음'),
                    ('VAL','고정값'),('CLOSE','종가'),
                ],
                default='MA',
            ),
        ),
        # operator에 'is' 추가
        migrations.AlterField(
            model_name='condition',
            name='operator',
            field=models.CharField(
                max_length=5,
                choices=[
                    ('gt','크다 (>)'),('lt','작다 (<)'),
                    ('gte','이상 (>=)'),('lte','이하 (<=)'),
                    ('is','조건 충족'),
                ],
                default='gte',
            ),
        ),
    ]

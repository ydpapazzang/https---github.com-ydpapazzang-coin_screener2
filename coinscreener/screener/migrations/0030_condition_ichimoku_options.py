from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('screener', '0029_keep_only_wolbongi_public_sample')]
    operations = [
        migrations.AddField(
            model_name='condition', name='closed_only',
            field=models.BooleanField(default=False, verbose_name='마감 완료봉만 사용'),
        ),
        migrations.AddField(
            model_name='condition', name='threshold_pct',
            field=models.FloatField(default=0.0, verbose_name='기준선 이격률 (%)'),
        ),
        migrations.AlterField(
            model_name='condition', name='left_indicator',
            field=models.CharField(choices=[('MA','단순이동평균(SMA)'),('EMA','지수이동평균(EMA)'),('WMA','가중이동평균(WMA)'),('RSI','RSI'),('BB_UPPER','볼린저 상단'),('BB_MIDDLE','볼린저 중단'),('BB_LOWER','볼린저 하단'),('HA_BULL','HA 양봉'),('HA_BEAR','HA 음봉'),('HA_BULL_N','HA 연속 양봉'),('HA_BEAR_N','HA 연속 음봉'),('HA_NO_LOWER','HA 아랫꼬리 없음'),('HA_NO_UPPER','HA 윗꼬리 없음'),('IC_TENKAN','일목 전환선'),('IC_KIJUN','일목 기준선'),('IC_SPAN_A','일목 선행스팬1'),('IC_SPAN_B','일목 선행스팬2'),('IC_CHIKOU','일목 후행스팬'),('IC_CHIKOU_REF','26봉 전 종가'),('IC_CLOUD_TOP','일목 구름대 상단'),('IC_CLOUD_BOTTOM','일목 구름대 하단'),('IC_PAST_CLOUD','26봉 전 구름대 상단'),('VAL','고정값'),('CLOSE','종가'),('CHANGE_RATE','당일 등락률(%)'),('VOLUME','거래량'),('VOLUME_PREV','이전봉 거래량'),('VOLUME_MA','평균 거래량')], default='MA', max_length=15),
        ),
        migrations.AlterField(
            model_name='condition', name='right_indicator',
            field=models.CharField(choices=[('MA','단순이동평균(SMA)'),('EMA','지수이동평균(EMA)'),('WMA','가중이동평균(WMA)'),('RSI','RSI'),('BB_UPPER','볼린저 상단'),('BB_MIDDLE','볼린저 중단'),('BB_LOWER','볼린저 하단'),('HA_BULL','HA 양봉'),('HA_BEAR','HA 음봉'),('HA_BULL_N','HA 연속 양봉'),('HA_BEAR_N','HA 연속 음봉'),('HA_NO_LOWER','HA 아랫꼬리 없음'),('HA_NO_UPPER','HA 윗꼬리 없음'),('IC_TENKAN','일목 전환선'),('IC_KIJUN','일목 기준선'),('IC_SPAN_A','일목 선행스팬1'),('IC_SPAN_B','일목 선행스팬2'),('IC_CHIKOU','일목 후행스팬'),('IC_CHIKOU_REF','26봉 전 종가'),('IC_CLOUD_TOP','일목 구름대 상단'),('IC_CLOUD_BOTTOM','일목 구름대 하단'),('IC_PAST_CLOUD','26봉 전 구름대 상단'),('VAL','고정값'),('CLOSE','종가'),('CHANGE_RATE','당일 등락률(%)'),('VOLUME','거래량'),('VOLUME_PREV','이전봉 거래량'),('VOLUME_MA','평균 거래량')], default='MA', max_length=15),
        ),
    ]


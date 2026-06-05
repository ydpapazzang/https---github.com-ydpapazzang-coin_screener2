from django.db import models


class Strategy(models.Model):
    name = models.CharField(max_length=100, verbose_name="전략명")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Condition(models.Model):
    TIMEFRAME_CHOICES = [
        ('minute1',   '1분봉'),
        ('minute3',   '3분봉'),
        ('minute5',   '5분봉'),
        ('minute10',  '10분봉'),
        ('minute15',  '15분봉'),
        ('minute30',  '30분봉'),
        ('minute60',  '1시간봉'),
        ('minute240', '4시간봉'),
        ('day',       '일봉'),
        ('week',      '주봉'),
        ('month',     '월봉'),
    ]
    INDICATOR_CHOICES = [
        ('MA',        '단순이동평균(SMA)'),
        ('EMA',       '지수이동평균(EMA)'),
        ('WMA',       '가중이동평균(WMA)'),
        ('RSI',       'RSI'),
        ('BB_UPPER',  '볼린저 상단'),
        ('BB_MIDDLE', '볼린저 중단'),
        ('BB_LOWER',  '볼린저 하단'),
        # 하이킨아시 전용 지표 (right_indicator에 사용)
        ('HA_BULL',        'HA 양봉'),
        ('HA_BEAR',        'HA 음봉'),
        ('HA_BULL_N',      'HA 연속 양봉'),
        ('HA_BEAR_N',      'HA 연속 음봉'),
        ('HA_NO_LOWER',    'HA 아랫꼬리 없음'),
        ('HA_NO_UPPER',    'HA 윗꼬리 없음'),
        # 일목균형표 지표
        ('IC_TENKAN',      '일목 전환선'),
        ('IC_KIJUN',       '일목 기준선'),
        ('IC_SPAN_A',      '일목 선행스팬1'),
        ('IC_SPAN_B',      '일목 선행스팬2'),
        ('IC_CHIKOU',      '일목 후행스팬'),
        ('IC_CHIKOU_REF',  '26봉 전 종가'),
        ('VAL',       '고정값'),
        ('CLOSE',     '종가'),
    ]
    OPERATOR_CHOICES = [
        ('gt',  '크다 (>)'),
        ('lt',  '작다 (<)'),
        ('gte', '이상 (>=)'),
        ('lte', '이하 (<=)'),
        # 하이킨아시 패턴 전용
        ('is',  '조건 충족'),
    ]

    strategy        = models.ForeignKey(Strategy, on_delete=models.CASCADE, related_name='conditions')
    timeframe       = models.CharField(max_length=20, choices=TIMEFRAME_CHOICES, default='day')
    offset          = models.IntegerField(default=0, verbose_name="n봉 전")
    offset_mode     = models.CharField(max_length=20, null=True, blank=True)
    left_indicator  = models.CharField(max_length=15, choices=INDICATOR_CHOICES, default='MA')
    left_param      = models.IntegerField(default=5)
    operator        = models.CharField(max_length=5, choices=OPERATOR_CHOICES, default='gte')
    right_indicator = models.CharField(max_length=15, choices=INDICATOR_CHOICES, default='MA')
    right_param     = models.IntegerField(default=20)
    bb_std          = models.FloatField(null=True, blank=True)

    def __str__(self):
        return f"{self.offset}봉전 {self.left_indicator}({self.left_param}) {self.operator} {self.right_indicator}({self.right_param})"


class AlertSetting(models.Model):
    """전략별 텔레그램 자동 알림 설정"""
    strategy    = models.OneToOneField(Strategy, on_delete=models.CASCADE, related_name='alert')
    enabled     = models.BooleanField(default=False, verbose_name="자동 알림 활성화")
    alert_hour  = models.IntegerField(default=9,  verbose_name="알림 시각 (시)")
    alert_min   = models.IntegerField(default=0,  verbose_name="알림 시각 (분)")
    exchange    = models.CharField(max_length=20, default='upbit')
    vol_limit   = models.IntegerField(default=100)
    updated_at  = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.strategy.name} 알림"
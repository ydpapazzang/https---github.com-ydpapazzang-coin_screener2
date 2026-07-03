from django.db import models


class Strategy(models.Model):
    name = models.CharField(max_length=100, verbose_name="전략명")
    win_rate = models.FloatField(default=60.0, verbose_name="승률 (%)")
    stop_loss = models.FloatField(default=-8.0, verbose_name="손절 기준 (%)")
    take_profit = models.FloatField(default=24.0, verbose_name="목표 익절 (%)")
    capital_pct = models.IntegerField(default=20, verbose_name="진입 자본 비율 (%)")
    created_at = models.DateTimeField(auto_now_add=True)

    def get_meta_text(self):
        conds = self.conditions.all()
        if not conds:
            return "조건 없음"
        
        tfs = []
        tf_map = {
            'minute1': '1분', 'minute3': '3분', 'minute5': '5분',
            'minute10': '10분', 'minute15': '15분', 'minute30': '30분',
            'minute60': '1시간', 'minute240': '4시간',
            'day': '일봉', 'week': '주봉', 'month': '월봉'
        }
        for c in conds:
            tf_readable = tf_map.get(c.timeframe, c.timeframe)
            if tf_readable not in tfs:
                tfs.append(tf_readable)
                
        inds = []
        ind_map = {
            'MA': 'MA', 'EMA': 'EMA', 'WMA': 'WMA', 'RSI': 'RSI',
            'BB_UPPER': '볼린저밴드', 'BB_MIDDLE': '볼린저밴드', 'BB_LOWER': '볼린저밴드',
            'HA_BULL': '하이킨아시', 'HA_BEAR': '하이킨아시', 'HA_BULL_N': '하이킨아시', 'HA_BEAR_N': '하이킨아시', 'HA_NO_LOWER': '하이킨아시', 'HA_NO_UPPER': '하이킨아시',
            'IC_TENKAN': '일목균형표', 'IC_KIJUN': '일목균형표', 'IC_SPAN_A': '일목균형표', 'IC_SPAN_B': '일목균형표', 'IC_SPAN_C': '일목균형표', 'IC_SPAN_D': '일목균형표', 'IC_CHIKOU': '일목균형표', 'IC_CHIKOU_REF': '일목균형표',
            'VOLUME': '거래량', 'VOLUME_PREV': '거래량', 'VOLUME_MA': '거래량'
        }
        for c in conds:
            ind_readable = ind_map.get(c.left_indicator, c.left_indicator)
            if ind_readable not in ('CLOSE', 'VAL') and ind_readable not in inds:
                inds.append(ind_readable)
            ind_readable_r = ind_map.get(c.right_indicator, c.right_indicator)
            if ind_readable_r not in ('CLOSE', 'VAL') and ind_readable_r not in inds:
                inds.append(ind_readable_r)
        
        tf_str = " · ".join(tfs)
        ind_str = " + ".join(inds) if inds else "종가"
        return f"{tf_str} · {ind_str}"

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
        # 거래량 지표
        ('VOLUME',      '거래량'),
        ('VOLUME_PREV', '이전봉 거래량'),
        ('VOLUME_MA',   '평균 거래량'),
    ]
    OPERATOR_CHOICES = [
        ('gt',  '크다 (>)'),
        ('lt',  '작다 (<)'),
        ('gte', '이상 (>=)'),
        ('lte', '이하 (<=)'),
        ('btw', '사이 (A <= X <= B)'),
        ('cross_up', '상향 돌파 (Cross Up)'),
        ('cross_down', '하향 돌파 (Cross Down)'),
        # 하이킨아시 패턴 전용
        ('is',  '조건 충족'),
    ]

    strategy        = models.ForeignKey(Strategy, on_delete=models.CASCADE, related_name='conditions')
    timeframe       = models.CharField(max_length=20, choices=TIMEFRAME_CHOICES, default='day')
    offset          = models.IntegerField(default=0, verbose_name="n봉 이내")
    offset_mode     = models.CharField(max_length=20, null=True, blank=True)
    left_indicator  = models.CharField(max_length=15, choices=INDICATOR_CHOICES, default='MA')
    left_param      = models.IntegerField(default=5)
    operator        = models.CharField(max_length=15, choices=OPERATOR_CHOICES, default='gte')
    right_indicator = models.CharField(max_length=15, choices=INDICATOR_CHOICES, default='MA')
    right_param     = models.IntegerField(default=20)
    bb_std          = models.FloatField(null=True, blank=True)

    def get_readable_text(self):
        left_lbl = self.get_left_indicator_display()
        right_lbl = self.get_right_indicator_display()
        
        op_map = {
            'gt': '초과(>)',
            'lt': '미만(<)',
            'gte': '이상(>=)',
            'lte': '이하(<=)',
            'btw': '사이(Between)',
            'cross_up': '상향돌파',
            'cross_down': '하향돌파',
            'is': '충족'
        }
        op_lbl = op_map.get(self.operator, self.operator)
        
        # 하이킨아시 패턴 포맷
        ha_patterns = ('HA_BULL', 'HA_BEAR', 'HA_BULL_N', 'HA_BEAR_N', 'HA_NO_LOWER', 'HA_NO_UPPER')
        if self.left_indicator in ha_patterns:
            if 'N' in self.left_indicator:
                return f"{self.offset}봉이내 {left_lbl}({self.left_param}봉 연속)"
            return f"{self.offset}봉이내 {left_lbl}"

        # 거래량 포맷
        if self.left_indicator == 'VOLUME':
            if self.right_indicator == 'VOLUME_PREV':
                pct = int(self.bb_std * 100) if self.bb_std else 100
                if self.operator == 'btw':
                    max_pct = self.left_param
                    return f"{self.offset}봉이내 거래량 {op_lbl} 이전봉 거래량의 {pct}% ~ {max_pct}%"
                return f"{self.offset}봉이내 거래량 {op_lbl} 이전봉 거래량의 {pct}%"
            elif self.right_indicator == 'VOLUME_MA':
                pct = int(self.bb_std * 100) if self.bb_std else 100
                if self.operator == 'btw':
                    max_pct = self.left_param
                    return f"{self.offset}봉이내 거래량 {op_lbl} 최근 {self.right_param}봉 평균 거래량의 {pct}% ~ {max_pct}%"
                return f"{self.offset}봉이내 거래량 {op_lbl} 최근 {self.right_param}봉 평균 거래량의 {pct}%"
        
        # 볼린저밴드 포맷
        if self.right_indicator in ('BB_UPPER', 'BB_MIDDLE', 'BB_LOWER'):
            std_val = self.bb_std if self.bb_std is not None else 2.0
            return f"{self.offset}봉이내 종가 {op_lbl} {right_lbl}({self.right_param}, {std_val}σ)"

        # 기본 포맷
        if self.operator == 'btw':
            max_val = int(self.bb_std) if self.bb_std is not None else 0
            left_part = f"{left_lbl}({self.left_param})" if self.left_indicator not in ('CLOSE', 'VAL') else left_lbl
            return f"{self.offset}봉이내 {left_part} 값이 {self.right_param} ~ {max_val} 사이"

        left_part = f"{left_lbl}({self.left_param})" if self.left_indicator not in ('CLOSE', 'VAL') else left_lbl
        if self.left_indicator == 'VAL': left_part = f"{self.left_param}"
        
        right_part = f"{right_lbl}({self.right_param})" if self.right_indicator not in ('CLOSE', 'VAL') else right_lbl
        if self.right_indicator == 'VAL': right_part = f"{self.right_param}"
        
        return f"{self.offset}봉이내 {left_part} {op_lbl} {right_part}"

    @property
    def get_volume_pct(self):
        return int(self.bb_std * 100) if self.bb_std is not None else 100

    def __str__(self):
        return self.get_readable_text()



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


class MarketData(models.Model):
    exchange = models.CharField(max_length=20, db_index=True) # e.g. 'kospi', 'upbit', 'bithumb'
    ticker = models.CharField(max_length=50, db_index=True)   # e.g. '005930', 'KRW-BTC'
    name = models.CharField(max_length=100)
    close_price = models.FloatField(default=0)
    volume = models.FloatField(default=0)
    amount = models.FloatField(default=0)
    market_cap = models.BigIntegerField(null=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('exchange', 'ticker')
        indexes = [
            models.Index(fields=['exchange', 'ticker']),
        ]

    def __str__(self):
        return f"[{self.exchange}] {self.name} ({self.ticker})"


class AlertHistory(models.Model):
    """알림 발송 및 스캔 매칭 이력"""
    strategy = models.ForeignKey(Strategy, on_delete=models.CASCADE, related_name='histories')
    symbol = models.CharField(max_length=20, verbose_name="코인 심볼")
    price = models.FloatField(verbose_name="진입 가격", null=True, blank=True)
    volume = models.FloatField(verbose_name="거래대금", default=0)
    details = models.TextField(verbose_name="매칭 상세", blank=True)
    status = models.CharField(max_length=20, default='new', verbose_name="매칭 상태")  # 'new', 'maintained'
    is_notified = models.BooleanField(default=True, verbose_name="텔레그램 발송 여부")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="발송 시각")

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.strategy.name} - {self.symbol} ({self.created_at})"
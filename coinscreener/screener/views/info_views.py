"""정적 정보 페이지 (소개/개인정보/약관/문의) + 가이드 글.

애드센스 승인 및 신뢰도 확보를 위한 오리지널 텍스트 콘텐츠.
가이드 본문은 뼈대(skeleton) 상태로, 이후 살을 붙여 확장한다.
"""
from django.shortcuts import render
from django.http import Http404

# 문의/개인정보 담당 연락처 (애드센스는 연락 수단을 요구)
CONTACT_EMAIL = 'laureatelim@gmail.com'


def more_menu(request):
    return render(request, 'screener/more.html')


def about(request):
    return render(request, 'screener/about.html', {'email': CONTACT_EMAIL})


def privacy(request):
    return render(request, 'screener/privacy.html', {'email': CONTACT_EMAIL})


def terms(request):
    return render(request, 'screener/terms.html', {'email': CONTACT_EMAIL})


def contact(request):
    return render(request, 'screener/contact.html', {'email': CONTACT_EMAIL})


# ─────────────────────────── 가이드 글 ───────────────────────────
# 뼈대 상태의 교육용 콘텐츠. slug로 상세 페이지를 연다.
GUIDES = [
    {
        'slug': 'volatility-breakout',
        'title': '래리 윌리엄스 변동성 돌파 전략이란?',
        'summary': '전일 변동폭을 이용해 당일 상승 초입을 노리는 대표적 단타 전략의 원리와 K값 개념.',
        'body': """
<p>변동성 돌파(Volatility Breakout)는 트레이더 래리 윌리엄스가 널리 알린 단기 매매 전략입니다.
전일의 고가와 저가 차이(변동폭)에 일정 비율 <b>K</b>를 곱한 값을, 당일 시가에 더한 가격을
<b>목표 매수가</b>로 잡습니다.</p>

<h2>계산 공식</h2>
<div class="box">
목표가 = 당일 시가 + (전일 고가 − 전일 저가) × K
</div>
<p>가격이 이 목표가를 <b>돌파</b>하면, 그날 상승 추세가 시작될 확률이 높다고 보고 진입합니다.
K는 보통 0.3~0.7 사이를 쓰며, 값이 작을수록 자주·빨리 진입하고 클수록 신중하게 진입합니다.</p>

<h2>이 서비스에서의 활용</h2>
<p>본 스크리너의 '오늘의 단타 추천'은 최근 구간을 백테스트해 종목별로 성과가 좋았던 K값을
자동으로 찾아 적용합니다. 또한 비트코인 시장 상황(추세·급락 여부)을 먼저 점검해
하락장에서는 추천을 쉬는 <b>시장 필터</b>를 함께 사용합니다.</p>

<h2>주의</h2>
<p>돌파 전략은 횡보장·급변장에서 잦은 손절(휩쏘)을 겪을 수 있습니다. 반드시 손절 기준을
정하고, 분할·소액으로 검증하며 사용하세요.</p>
""",
    },
    {
        'slug': 'rsi',
        'title': 'RSI(상대강도지수) 쉽게 이해하기',
        'summary': '과매수·과매도를 판단하는 대표 보조지표. 수치의 의미와 매매 활용법.',
        'body': """
<p>RSI(Relative Strength Index)는 최근 일정 기간의 상승폭과 하락폭을 비교해
<b>0~100</b> 사이 값으로 나타내는 모멘텀 지표입니다. 기본 기간은 14봉을 많이 씁니다.</p>

<h2>수치의 의미</h2>
<ul>
<li><b>70 이상</b> — 과매수 구간. 단기 조정 가능성.</li>
<li><b>30 이하</b> — 과매도 구간. 단기 반등 가능성.</li>
<li><b>50 부근</b> — 중립. 추세의 강약을 가르는 기준선.</li>
</ul>

<h2>활용 팁</h2>
<p>RSI는 단독보다 추세와 함께 봐야 합니다. 강한 상승장에서는 70을 넘고도 계속 오르고,
강한 하락장에서는 30 아래에서도 더 빠질 수 있습니다. 본 스크리너에서는 조건식에 RSI를 넣어
'RSI 50 상향 돌파' 같은 신호를 전체 종목에서 한 번에 찾을 수 있습니다.</p>
""",
    },
    {
        'slug': 'ichimoku',
        'title': '일목균형표(구름대) 기초',
        'summary': '전환선·기준선·선행스팬·구름대의 뜻과 추세 판단에 쓰는 법.',
        'body': """
<p>일목균형표는 여러 개의 선과 '구름대(Cloud)'로 추세·지지·저항을 한눈에 보여주는 지표입니다.</p>

<h2>구성 요소</h2>
<ul>
<li><b>전환선</b> — 단기 균형선(9봉 기준).</li>
<li><b>기준선</b> — 중기 균형선(26봉 기준).</li>
<li><b>선행스팬 1·2</b> — 두 선 사이 영역이 <b>구름대</b>가 됩니다.</li>
<li><b>후행스팬</b> — 현재 종가를 과거로 이동시켜 그린 선.</li>
</ul>

<h2>해석</h2>
<p>가격이 <b>구름대 위</b>에 있으면 상승 우위, <b>아래</b>면 하락 우위로 봅니다.
가격이 구름대를 <b>상향 돌파</b>하면 추세 전환 신호로 해석하는 경우가 많습니다.
본 스크리너는 '구름대 상향 돌파' 같은 조건을 전체 마켓에서 자동 검색해 줍니다.</p>
""",
    },
    {
        'slug': 'how-to-use',
        'title': '스크리너 사용법 — 조건식으로 종목 찾기',
        'summary': '전략(조건식)을 만들고 전체 코인·ETF에서 신호가 뜬 종목을 걸러내는 방법.',
        'body': """
<p>스크리너는 내가 정한 <b>조건식</b>에 맞는 종목을 전체 시장에서 한 번에 찾아 주는 도구입니다.</p>

<h2>기본 흐름</h2>
<ul>
<li>① <b>전략 만들기</b> — 홈에서 새 전략을 추가합니다.</li>
<li>② <b>조건 추가</b> — 타임프레임(일봉·15분봉 등)과 지표(EMA·RSI·일목 등),
비교 연산(돌파·이상·이하)을 조합합니다.</li>
<li>③ <b>검색</b> — 업비트·빗썸·코스피(ETF)에서 조건을 만족하는 종목이 즉시 나옵니다.</li>
<li>④ <b>알림 설정</b> — 원하면 매일 특정 시각에 텔레그램으로 결과를 받을 수 있습니다.</li>
</ul>

<h2>백테스트</h2>
<p>특정 종목에 전략을 적용했을 때 과거 성과(승률·누적수익·MDD)를 시뮬레이션으로 확인할 수 있습니다.
실매매 전 검증 용도로 활용하세요.</p>
""",
    },
]

GUIDE_MAP = {g['slug']: g for g in GUIDES}


def guide_list(request):
    return render(request, 'screener/guide_list.html', {'guides': GUIDES})


def guide_detail(request, slug):
    g = GUIDE_MAP.get(slug)
    if not g:
        raise Http404('가이드를 찾을 수 없습니다.')
    return render(request, 'screener/guide_detail.html', {'g': g, 'guides': GUIDES})

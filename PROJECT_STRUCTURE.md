# 🪙 Woniiscreener 프로젝트 구조 명세서 (Project Architecture)

본 문서는 구글 클라우드(GCP) 이전 이후, 최적화가 완료된 현재 시점의 시스템 구조와 핵심 모듈의 역할을 정리한 문서입니다.

## 🏗 인프라 구조 (Infrastructure)
* **호스팅:** 구글 클라우드 플랫폼 (GCP e2-micro 인스턴스 / Ubuntu)
* **도메인 & 보안:** `woniiscreener.duckdns.org` + Let's Encrypt (HTTPS 적용 완료)
* **리버스 프록시:** Nginx (80/443 포트 수신 ➔ Django 8000 포트로 전달)
* **데이터베이스:** SQLite (`db.sqlite3`) - 단일 소스 캐시 DB로 활용
* **백그라운드 서비스 (Systemd):**
  1. `coinscreener.service`: Django 웹 애플리케이션 구동 (`manage.py runserver 0.0.0.0:8000`)
  2. `upbit-crawler.service`: 업비트 & 빗썸 24시간 5분 주기 크롤러
  3. `kospi-crawler.service`: 코스피/ETF 전용 1시간 주기 크롤러 (평일 09:00~15:30 전용)

---

## 📁 주요 폴더 및 핵심 파일 설명 (Code Structure)

```text
coin-screener/
├── coinscreener/
│   ├── settings.py           # 전체 환경 설정 (CSRF, ALLOWED_HOSTS, python-dotenv 로드)
│   ├── urls.py               # 메인 URL 라우팅
│   └── screener/
│       ├── models.py         # 데이터베이스 모델 (Condition, Strategy, AlertSetting, OHLCVCache)
│       ├── telegram.py       # 텔레그램 알림 발송 유틸리티 (메시지 렌더링, 딥링크 처리)
│       ├── management/
│       │   └── commands/     # 백그라운드 크롤링 봇 스크립트
│       │       ├── update_upbit_cache.py   # 코인(업비트,빗썸) 캐시 봇 (5분 무한 루프)
│       │       └── update_kospi_cache.py   # 코스피(ETF) 캐시 봇 (평일 낮 1시간 주기)
│       └── views/
│           ├── scan_views.py       # 스크리너 엔진 (DB 캐시 기반 종목 필터링 로직)
│           ├── cron_views.py       # 스케줄된 알림 발송 로직 (UptimeRobot 등에서 호출)
│           └── strategy_views.py   # 사용자의 전략 저장, 웹에서 1회 테스트 발송 요청 처리
├── db.sqlite3                # 로컬 데이터베이스 파일
├── requirements.txt          # 파이썬 라이브러리 목록 (pyupbit, pybithumb, FinanceDataReader, python-dotenv 등)
└── .env                      # (Git 제외) 텔레그램 토큰 등 민감한 환경 변수 저장소
```

## 🚀 시스템 동작 원리 (Data Flow)

1. **데이터 수집 (Background Bots):**
   * `update_upbit_cache`와 `update_kospi_cache`가 각각 자신의 주기에 맞춰 거래소 API 및 네이버 증권을 호출합니다.
   * 가져온 OHLCV(시가,고가,저가,종가,거래량) 데이터는 `OHLCVCache`라는 SQLite 테이블에 영구 저장됩니다. (과거의 실시간 API 의존성 완전 탈피)

2. **종목 검색 및 웹 조회 (Frontend -> Django):**
   * 사용자가 화면에서 검색 버튼을 누르거나 조건식을 저장하면 `scan_views.py`가 호출됩니다.
   * `scan_views.py`는 실시간 API를 전혀 호출하지 않고, 오직 SQLite(`OHLCVCache`)에 이미 차곡차곡 쌓여있는 최신 데이터만 빠르게 읽어와서 수 초 내에 결과를 화면에 뿌려줍니다.

3. **텔레그램 알림 발송 (Telegram Alert):**
   * 봇이 텔레그램 메시지를 보낼 때는 `telegram.py`를 거치게 됩니다.
   * 알림 하단에는 `🔗 웹사이트로이동하기` 형식의 깔끔한 링크가 삽입되며, 스마트폰 앱 딥링크 연결도 지원합니다.
   * 텔레그램 토큰(비밀번호)은 깃허브에 노출되지 않도록 서버 내의 `.env` 파일에 안전하게 보관됩니다.

## 📝 관리자 명령어 가이드
새로운 코드를 업데이트하거나 서버가 멈췄을 때 사용하는 명령어들입니다.

```bash
# 최신 코드 깃허브에서 가져오기
git pull origin main

# Nginx(문지기) 재시작
sudo systemctl restart nginx

# 장고 웹서버 재시작
sudo systemctl restart coinscreener

# 코인 봇(5분 주기) 재시작 및 로그 보기
sudo systemctl restart upbit-crawler
sudo journalctl -u upbit-crawler -n 50 -f

# 코스피 봇(1시간 주기) 재시작 및 로그 보기
sudo systemctl restart kospi-crawler
sudo journalctl -u kospi-crawler -n 50 -f
```

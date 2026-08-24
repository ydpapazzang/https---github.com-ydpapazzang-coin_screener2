# Woniiscreener 프로젝트 구조 및 운영 명세

최종 갱신: 2026-08-24

이 문서는 현재 GCP 운영 서버와 `main` 브랜치를 기준으로 합니다. Vercel은 사용하지 않습니다.

## 1. 운영 인프라

- 호스팅: GCP e2-micro Ubuntu VM
- 프로젝트 경로: `/home/i_am_psw86/myapp`
- 도메인: `https://woniiscreener.duckdns.org`
- HTTPS: Let's Encrypt
- 리버스 프록시: Nginx → `127.0.0.1:8000`
- 웹 애플리케이션: Gunicorn, gthread worker 1개·thread 2개
- 데이터베이스: 로컬 SQLite `db.sqlite3`
- SQLite 동시성: WAL, 30초 busy timeout, `synchronous=NORMAL`
- 환경변수: `/home/i_am_psw86/myapp/.env`, 권한 600

필수 환경변수:

- `DJANGO_SECRET_KEY`
- `CRON_SECRET`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `ALLOWED_HOSTS` 필요 시 설정

## 2. 운영 프로세스와 스케줄

### 상시 systemd 서비스

| 서비스 | 역할 |
|---|---|
| `coinscreener.service` | Gunicorn Django 웹 서버 |
| `upbit-crawler.service` | 업비트·빗썸 OHLCV 수집과 독립 60초 단타·스윙 성적 추적 |
| `kospi-crawler.service` | KOSPI·ETF 데이터 수집 |

### systemd timer

| 타이머 | 실행 시각(KST) | 역할 |
|---|---:|---|
| `coinscreener-backup.timer` | 03:30 | 검증된 SQLite 온라인 백업, 최근 14개 보관 |
| `coinscreener-daily-picks.timer` | 09:00, 09:10 | 단타 추천 생성과 실패 재시도 |
| `coinscreener-swing-picks.timer` | 09:20, 09:35, 09:50 | 스윙 추천 생성과 실패 재시도 |

단타와 스윙의 시작 시간을 분리해 e2-micro에서 두 분석 작업이 동시에 실행되지 않게 합니다.

### 외부 cron-job.org

cron-job.org는 `/cron/scan/`만 30분마다 호출합니다. 인증 정보는 URL이 아닌 다음 헤더로 전달합니다.

```text
Authorization: Bearer <CRON_SECRET>
```

다음 원격 생성 엔드포인트는 중복 실행을 막기 위해 제거되었습니다.

- `/cron/daily-picks/`
- `/cron/swing-picks/`

서버 사용자 crontab에는 방문 로그 정리만 남깁니다. 매분 `git pull`과 `generate_daily_picks` 항목은 사용하지 않습니다. 배포는 항상 명시적으로 수행합니다.

세부 절차: `deploy/SCHEDULING.md`

## 3. 주요 코드 구조

```text
coin-screener/
├── coinscreener/
│   ├── settings.py
│   ├── urls.py
│   └── screener/
│       ├── apps.py
│       ├── models.py
│       ├── engine.py
│       ├── backtest.py
│       ├── daily_picks.py
│       ├── swing_strategy.py
│       ├── sqlite_maintenance.py
│       ├── telegram.py
│       ├── management/commands/
│       │   ├── update_upbit_cache.py
│       │   ├── update_kospi_cache.py
│       │   ├── generate_daily_picks.py
│       │   ├── generate_swing_picks.py
│       │   ├── backup_sqlite.py
│       │   └── restore_sqlite.py
│       └── views/
│           ├── scan_views.py
│           ├── cron_views.py
│           ├── strategy_views.py
│           └── backtest_views.py
├── deploy/
│   ├── SCHEDULING.md
│   └── systemd/
├── backups/                 # Git 제외, 운영 백업
├── db.sqlite3               # Git 제외
├── requirements.txt
└── .env                     # Git 제외
```

## 4. 데이터 흐름

### 시세 캐시

`update_upbit_cache`는 활성 전략에서 필요한 시간봉을 찾아 업비트와 빗썸을 거래소별로 수집합니다.

- 외부 API 조회와 지표 사전 계산은 제한된 작업 스레드에서 실행
- 거래소별 요청 제한 준수
- 실패한 요청은 최대 3회 지수 백오프로 재시도
- 업비트 장애가 빗썸 수집을 막지 않도록 거래소별 격리
- SQLite 쓰기는 메인 크롤러 스레드에서 순차 실행
- 오류 상세는 거래소당 20건까지 기록하고 나머지는 요약
- `CRAWLER_START`, `CRAWLER_PROGRESS`, `CRAWLER_EXCHANGE_SUMMARY`, `CRAWLER_CYCLE_SUMMARY` 로그 제공

`OHLCVCache.updated_at`이 시간봉별 허용 기간보다 오래되면 검색 경로는 해당 캐시를 신뢰하지 않고 라이브 갱신 대상으로 처리합니다.

### 추천 성적 추적

전체 시세 수집이 오래 걸려도 단타·스윙 성적 추적은 별도 스레드에서 60초마다 실행됩니다.

- 단타: 진입·목표·손절과 진입 후 최고가 추적
- 스윙: 누락 1분봉 재생, 진입 만료, 2R 부분익절, EMA20·3ATR 추적손절, 기간 종료
- API 실패 시 마지막 확인 시각을 전진시키지 않아 다음 주기에 같은 구간 재시도

### 추천 생성

단타:

- 변동성 돌파와 최근 백테스트 기반 K값
- BTC 추세·급락·RSI 방어 필터
- 목표 수익률 +2%, 손절 -1.5%

스윙:

- BTC 일봉 상승 국면에서만 생성
- 유동성, EMA20·EMA60, 모멘텀, 돌파, ATR 기준
- 최대 3종목, 신호 2일 후 만료
- 2R에서 50% 부분익절 후 추적손절
- 프로세스 파일 잠금과 DB 트랜잭션으로 중복·부분 저장 방지

## 5. 표준 배포

자동 `git pull`은 사용하지 않습니다.

```bash
cd ~/myapp
git pull origin main
git rev-parse HEAD

./venv/bin/pip install -r requirements.txt
./venv/bin/python manage.py migrate
./venv/bin/python manage.py check
./venv/bin/python manage.py test -v 1

sudo systemctl restart coinscreener
sudo systemctl restart upbit-crawler
sudo systemctl restart kospi-crawler
```

systemd 파일이 변경된 배포에서는 해당 파일을 `/etc/systemd/system/`에 설치하고 `daemon-reload` 후 timer를 활성화합니다.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now \
  coinscreener-backup.timer \
  coinscreener-daily-picks.timer \
  coinscreener-swing-picks.timer
```

## 6. SQLite 백업과 복구

### 수동 백업

서비스를 중지하지 않아도 SQLite 온라인 백업 API가 일관된 스냅샷을 생성합니다.

```bash
cd ~/myapp
./venv/bin/python manage.py backup_sqlite --keep 14
ls -lh backups/
```

백업은 `PRAGMA integrity_check`를 통과한 뒤에만 최종 파일명으로 원자적으로 이동됩니다.

### 복구

복구는 쓰기 프로세스를 모두 중지해야 합니다.

```bash
cd ~/myapp
sudo systemctl stop coinscreener upbit-crawler kospi-crawler

./venv/bin/python manage.py restore_sqlite \
  backups/coin-screener-YYYYMMDD-HHMMSS-ffffff.sqlite3 \
  --confirm-services-stopped

./venv/bin/python manage.py migrate

sudo systemctl start coinscreener upbit-crawler kospi-crawler
sudo systemctl status coinscreener --no-pager -l
```

복구 명령은 원본 백업을 먼저 검증하고, 현재 DB를 `pre-restore-*.sqlite3`로 보존한 뒤 임시 파일을 원자적으로 교체합니다.

## 7. 운영 확인 명령

```bash
# 서비스
systemctl --no-pager --full status \
  coinscreener upbit-crawler kospi-crawler

# 타이머
systemctl list-timers \
  coinscreener-backup.timer \
  coinscreener-daily-picks.timer \
  coinscreener-swing-picks.timer \
  --all --no-pager

# 크롤러 최근 사이클
sudo journalctl -u upbit-crawler --since "30 minutes ago" --no-pager \
  | grep -E 'CRAWLER_|Cycle finished|monitor error'

# 백업
sudo journalctl -u coinscreener-backup.service -n 50 --no-pager
ls -lh backups/

# Django 설정 및 마이그레이션
./venv/bin/python manage.py check
./venv/bin/python manage.py showmigrations
```

## 8. 운영 원칙

- 비밀값을 URL query string이나 Git에 넣지 않습니다.
- 코드 배포와 systemd 설정 배포를 구분합니다.
- 모델 변경 후에는 반드시 migration과 테스트를 실행합니다.
- 크롤러 변경 후에는 `upbit-crawler`를 재시작합니다.
- 백업 성공은 파일 존재만으로 판단하지 않고 무결성 검사와 systemd 로그로 확인합니다.
- 복구 명령은 서비스 중지와 복구 전 스냅샷 없이 실행하지 않습니다.

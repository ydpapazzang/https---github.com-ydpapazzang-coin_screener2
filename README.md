# 🪙 Coin Screener

## 📌 프로젝트 소개

**Coin Screener**는 암호화폐 시장 데이터를 수집하고 분석하여 투자 기회를 식별하는 웹 애플리케이션입니다. 
Django 백엔드와 HTML 프론트엔드로 구성되어 있으며, Vercel을 통해 배포됩니다.

**배포 주소**: https://https-github-com-ydpapazzang-coin-s.vercel.app

---

## 🏗️ 프로젝트 구조

```
coin_screener/
├── coinscreener/              # Django 메인 애플리케이션
│   ├── wsgi.py               # Vercel 배포용 WSGI 설정
│   ├── settings.py           # Django 설정
│   └── urls.py               # URL 라우팅
├── manage.py                 # Django 관리 유틸리티
├── requirements.txt          # Python 의존성
├── vercel.json              # Vercel 배포 설정
└── README.md                # 프로젝트 문서
```

---

## 💻 기술 스택

### 백엔드
- **Django 4.2.0**: 웹 프레임워크
- **Python**: 프로그래밍 언어..

### 데이터 처리
- **pyupbit 0.2.34**: 암호화폐 시장 데이터 API
- **pandas**: 데이터 분석 및 처리
- **numpy**: 수치 계산

### 프론트엔드
- **HTML**: 사용자 인터페이스 (63.9%)

### 데이터베이스 & 배포
- **PostgreSQL**: 데이터 저장 (psycopg2-binary)
- **Vercel**: 클라우드 배포 플랫폼
- **python-dotenv**: 환경 변수 관리
- **dj-database-url**: 데이터베이스 설정

---

## 📊 언어 구성

- **HTML**: 63.9% - 프론트엔드 UI
- **Python**: 36.1% - 백엔드 로직

---

## 🚀 시작하기

### 설치

```bash
# 저장소 클론
git clone https://github.com/ydpapazzang/https---github.com-ydpapazzang-coin_screener2.git
cd coin_screener

# 가상 환경 생성 (권장)
python -m venv venv

# 가상 환경 활성화
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

# 의존성 설치
pip install -r requirements.txt
```

### 환경 설정

`.env` 파일을 생성하여 필요한 환경 변수를 설정하세요:

```
DATABASE_URL=your_postgresql_url
SECRET_KEY=your_secret_key
DEBUG=False
```

### 실행

```bash
# 데이터베이스 마이그레이션
python manage.py migrate

# 개발 서버 시작
python manage.py runserver
```

---

## 🔌 주요 기능

- ✅ 암호화폐 시장 데이터 수집 (upbit API)
- ✅ 실시간 시세 분석
- ✅ 데이터 시각화
- ✅ 투자 기회 식별
- ✅ RESTful API 엔드포인트

---

## 📦 의존성

| 패키지 | 버전 | 용도 |
|--------|------|------|
| Django | 4.2.0 | 웹 프레임워크 |
| pyupbit | 0.2.34 | 암호화폐 시장 데이터 |
| pandas | 최신 | 데이터 분석 |
| numpy | 최신 | 수치 계산 |
| python-dotenv | 1.0.0 | 환경 변수 관리 |
| dj-database-url | 최신 | 데이터베이스 설정 |
| psycopg2-binary | 최신 | PostgreSQL 드라이버 |

---

## 🌐 배포

이 프로젝트는 **Vercel**을 통해 배포되며, `vercel.json` 설정으로 자동 배포됩니다.

### Vercel 배포 설정
- **빌드**: Python 런타임 사용
- **진입점**: `coinscreener/wsgi.py`
- **라우팅**: 모든 요청을 Django 애플리케이션으로 전달

---

## 📝 라이선스

이 프로젝트는 자유롭게 사용, 수정, 배포할 수 있습니다.

---

## 👤 작성자

**ydpapazzang**

---

## 📞 연락처

문의사항이 있으시면 GitHub Issues를 통해 연락 주세요.

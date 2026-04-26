# 공모주 투자 대시보드

DART 공시 데이터를 수집해 공모주 청약 일정·AI 예측·커뮤니티 반응을 한눈에 보여주는 자동화 대시보드입니다.
매일 오전 8시(KST) GitHub Actions가 데이터를 갱신하고, 텔레그램으로 청약 판단 브리핑을 전송합니다.

**대시보드** → https://lee9387-hm.github.io/ipo-investment-dashboard/dashboard/

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| 공모주 수집 | DART OpenAPI에서 최근 180일 증권신고서 자동 수집 |
| 상세 파싱 | 수요예측 경쟁률·의무보유확약·사업개요 추출 |
| AI 예측 | Gemini로 상장일 종가·고가·Bull/Bear 분석 |
| 실적 수집 | 상장 후 KIS API로 OHLCV 수집 및 예측 정확도 계산 |
| 커뮤니티 감성 | 네이버 뉴스·블로그 API로 종목별 여론 수집 |
| 텔레그램 알림 | 청약 판단 카드(강력추천/검토/중립/보류) 일일 브리핑 |
| 대시보드 | GitHub Pages — 공모주/예측/결과/정확도 4탭, 모바일 대응 |

---

## 아키텍처

```
DART OpenAPI ──► collect.py ──► ipo_list.csv
                                    │
                    ┌───────────────┼────────────────┐
                    ▼               ▼                ▼
               details.py      analyze.py       predict.py
            (수요예측 파싱)   (상태·실주가)    (Gemini AI)
            (Naver 뉴스)      (정확도 계산)   (Bull/Bear)
                    │               │                │
                    ▼               ▼                ▼
              details/*.json   results.csv    predictions.csv
                    └───────────────┴────────────────┘
                                    │
                               notify.py ──► Telegram
                                    │
                             dashboard/ ──► GitHub Pages
```

---

## 디렉터리 구조

```
ipo-investment-dashboard/
├── .github/workflows/
│   ├── daily.yml       # 매일 08:00 KST — 수집·분석·예측·알림
│   ├── weekly.yml      # 매주 월요일 — 주간 요약
│   └── pages.yml       # push 시 GitHub Pages 자동 배포
├── data/
│   ├── ipo_list.csv        # 공모주 목록 (DART 기준)
│   ├── predictions.csv     # Gemini AI 예측 결과
│   ├── results.csv         # 실제 상장 결과 (KIS OHLCV)
│   ├── accuracy_log.csv    # 예측 정확도 로그
│   ├── underwriter_stats.csv  # 주관사별 과거 실적
│   └── details/            # 종목별 상세 JSON (rcept_no 기준)
├── scripts/
│   ├── collect.py      # DART 공시 수집
│   ├── analyze.py      # 상태 갱신 + KIS 실주가 + 정확도
│   ├── details.py      # 수요예측·뉴스·커뮤니티 수집
│   ├── predict.py      # Gemini AI 예측
│   └── notify.py       # 텔레그램 알림
├── dashboard/
│   ├── index.html      # 4탭 대시보드
│   ├── index.css       # Coinbase 디자인 시스템 (다크 테마)
│   └── main.js         # CSV 로딩·렌더링·모달
├── DESIGN.md           # Coinbase 디자인 토큰 참조
└── requirements.txt
```

---

## 설치 및 로컬 실행

### 사전 요구사항
- Python 3.11+
- API 키 (아래 [필요한 API 키](#필요한-api-키) 참고)

### 설정

```bash
git clone https://github.com/LEE9387-HM/ipo-investment-dashboard.git
cd ipo-investment-dashboard
pip install -r requirements.txt
```

`.env.local` 파일 생성:

```env
DART_API_KEY=...
GEMINI_API_KEY=...
KIS_APP_KEY=...
KIS_APP_SECRET=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
NAVER_CLIENT_ID=...
NAVER_CLIENT_SECRET=...
```

### 수동 실행 순서

```bash
python scripts/collect.py   # 1. DART 공시 수집
python scripts/analyze.py   # 2. 상태 갱신 + KIS 실주가
python scripts/details.py   # 3. 수요예측 + 뉴스 수집
python scripts/predict.py   # 4. Gemini AI 예측
python scripts/notify.py daily  # 5. 텔레그램 브리핑
```

---

## 필요한 API 키

| 환경변수 | 발급처 | 무료 여부 |
|---------|--------|----------|
| `DART_API_KEY` | [DART OpenAPI](https://opendart.fss.or.kr) | 무료 |
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/apikey) | 무료 (쿼터 제한) |
| `KIS_APP_KEY` / `KIS_APP_SECRET` | [한국투자증권 Open API](https://apiportal.koreainvestment.com) | 무료 |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) | 무료 |
| `TELEGRAM_CHAT_ID` | 봇 시작 후 `getUpdates` 확인 | — |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | [네이버 개발자 센터](https://developers.naver.com) | 무료 |

---

## GitHub Actions 자동화

GitHub 저장소 Settings → Secrets and variables → Actions 에서 위 키를 등록하면 자동 실행됩니다.

| 워크플로우 | 실행 시각 | 내용 |
|-----------|----------|------|
| `daily.yml` | 매일 08:00 KST | 수집 → 분석 → 상세 → 예측 → 알림 |
| `weekly.yml` | 매주 월요일 08:00 KST | 주간 요약 + 정확도 갱신 |
| `pages.yml` | push/data 변경 시 | GitHub Pages 자동 배포 |

수동 실행: Actions 탭 → 워크플로우 선택 → **Run workflow**

---

## 대시보드

**공모주 현황** — 청약중·청약예정·상장예정 상태별 목록 + 행 클릭 시 상세 모달

**예측** — Gemini AI 예측 종가·고가·상승률 + Bull/Bear 분석

**상장 결과** — 실제 상장일 OHLCV + 수익률

**정확도** — 예측 대비 실제 오차 추이

모바일에서는 테이블이 카드 형태로 자동 전환됩니다.

---

## 텔레그램 알림 예시

```
📊 공모주 청약 브리핑
2026년 04월 27일

📅 7일 내 청약 예정 (2건)

🏢 ABC바이오  [코스닥]
💰 15,000~18,000원  |  청약 05/02~05/03 · 상장 05/09
🏦 주관사: 미래에셋증권
🤖 예측 32,000원 (+78%)  · 고신뢰
📊 경쟁률 1,423:1
🔒 확약 38%
  📈 높은 기관 경쟁률과 의무보유확약 비율
  📉 시장 변동성 확대 구간
  💬 [블로그] ABC바이오 공모주 청약 후기 — 경쟁률 대박...
판단: 🟢 강력 추천  (AI +78% (고신뢰) · 경쟁률 1,423:1 · 확약 38%)
```

---

## 데이터 출처

- **DART OpenAPI** — 금융감독원 전자공시시스템
- **한국투자증권 KIS Open API** — 실시간 주가 데이터
- **Google Gemini** — AI 예측 및 분석
- **네이버 검색 API** — 커뮤니티·블로그 반응

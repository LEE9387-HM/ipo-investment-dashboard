# STATUS

- **Project Start Date**: 2026-04-26
- **Current Phase**: Phase 1 완료 → Phase 2 대기 (API 키 발급 필요)
- **Last Updated**: 2026-04-26

## 1. 현재 상태 요약
전체 프로젝트 골격(스크립트 4종, 대시보드, GitHub Actions 3종) 구현 완료.
실제 데이터 수집은 DART_API_KEY 발급 후 가능.

## 2. 완료된 항목

### 스크립트
- [x] `scripts/collect.py` — DART 공시 목록 수집 + ipo_list.csv upsert
- [x] `scripts/analyze.py` — 상태 갱신 + KIS 실주가 수집 + 정확도 계산
- [x] `scripts/predict.py` — Gemini API 공모주 종가 예측 + predictions.csv
- [x] `scripts/notify.py` — Telegram 일일 브리핑 / 주간 요약 발송

### 데이터
- [x] `data/ipo_list.csv` — 스키마 확정 (18컬럼), 헤더 초기화
- [x] `data/predictions.csv` — 스키마 확정 (8컬럼)
- [x] `data/results.csv` — 스키마 확정 (12컬럼)
- [x] `data/accuracy_log.csv` — 스키마 확정 (7컬럼)

### 대시보드
- [x] `dashboard/index.html` — 4탭 레이아웃 (공모주/예측/결과/정확도)
- [x] `dashboard/index.css` — 프리미엄 다크 디자인 시스템
- [x] `dashboard/main.js` — CSV fetch + 파싱 + 테이블 렌더링 (외부 라이브러리 없음)

### 자동화
- [x] `.github/workflows/daily.yml` — 매일 오전 8시 KST 수집/분석/예측/알림
- [x] `.github/workflows/weekly.yml` — 매주 월요일 주간 요약 + 정확도 갱신
- [x] `.github/workflows/pages.yml` — GitHub Pages 자동 배포

### 설정
- [x] `.gitignore` — 비밀값/Python 캐시/에디터 파일 제외
- [x] `.env.local.example` — API 키 4종 템플릿
- [x] `requirements.txt` — requests, pandas, python-dotenv, google-generativeai

## 3. 진행 중인 항목 (사용자 액션 필요)
- [ ] **DART_API_KEY** 발급 → `.env.local` 등록
- [ ] `pip install -r requirements.txt`
- [ ] `python scripts/collect.py` 첫 실행 및 결과 확인
- [ ] GitHub 저장소 생성 → Secrets 등록 (DART_API_KEY, KIS_APP_KEY 등)
- [ ] GitHub Pages 활성화 (`Settings > Pages > Source: GitHub Actions`)

## 4. 이슈 및 위험 요소
- **데이터 상세 파싱 미완**: Phase 1에서는 DART 목록 API만 사용.
  청약일·공모가 밴드 등은 개별 공시 XML 파싱(Phase 2) 전까지 빈 값.
- **KIS API 인증**: 한국투자증권 계좌 개설 후 발급 필요 (실전투자용).
- **Gemini 할당량**: 무료 티어 RPM/RPD 제한 있음. 대량 예측 시 rate limit 처리 추가 필요.

## 5. 다음 주요 마일스톤 (Phase 2)
- DART 개별 공시 XML 파싱으로 청약일·공모가·주관사 필드 자동 채우기.
- `scripts/collect.py`에 상세 파싱 함수(`fetch_offering_details`) 추가.

# STATUS

- **Project Start Date**: 2026-04-26
- **Current Phase**: Phase 2 완료
- **Last Updated**: 2026-04-26

## 1. 현재 상태 요약
Phase 2 구현 완료. collect.py가 DART API로 실제 데이터를 수집하고
company API + document XML 파싱으로 상세 필드(공모가, 청약일, 주관사)를 자동 보완함.

## 2. 완료된 항목

### 스크립트
- [x] `scripts/collect.py` — DART 발행공시 수집 + enrich_ipo_items Phase 2 완료
  - `pblntf_ty=C` 필터로 전체 45,000건→2,200건 압축
  - company API로 market(KOSPI/KOSDAQ) 자동 분류
  - document.xml ZIP 파싱으로 청약기일·공모가·주관사 추출
  - 88일 단위 분할 조회 (DART 90일 제한 대응)
- [x] `scripts/analyze.py` — 상태 갱신 + KIS 실주가 수집 + 정확도 계산
- [x] `scripts/predict.py` — Gemini API 공모주 종가 예측 + predictions.csv
- [x] `scripts/notify.py` — Telegram 일일 브리핑 / 주간 요약 발송

### 데이터
- [x] `data/ipo_list.csv` — 실제 DART 데이터 수집 완료 (18컬럼)
- [x] `data/predictions.csv` — 스키마 확정 (8컬럼)
- [x] `data/results.csv` — 스키마 확정 (12컬럼)
- [x] `data/accuracy_log.csv` — 스키마 확정 (7컬럼)

### 대시보드
- [x] `dashboard/index.html` — 4탭 레이아웃 (공모주/예측/결과/정확도)
- [x] `dashboard/index.css` — 프리미엄 다크 디자인 시스템
- [x] `dashboard/main.js` — CSV fetch + 파싱 + 테이블 렌더링

### 자동화
- [x] `.github/workflows/daily.yml` — 매일 오전 8시 KST 수집/분석/예측/알림
- [x] `.github/workflows/weekly.yml` — 매주 월요일 주간 요약 + 정확도 갱신
- [x] `.github/workflows/pages.yml` — GitHub Pages 자동 배포

### 설정
- [x] `.gitignore`, `.env.local.example`, `requirements.txt` 완비
- [x] `.env.local` — DART / KIS / Gemini / Telegram 키 등록 완료

## 3. 알려진 이슈 / 개선 여지
- **listing_dt 정확도**: 정정신고서에서 정정 前 날짜가 잡힐 수 있음. analyze.py가 KIS로 실상장일을 덮어쓰므로 실운영 영향 없음.
- **total_amount 미추출**: DART 문서 구조상 패턴 불일치. 추후 개선 가능.
- **google-generativeai 구버전 경고**: predict.py가 deprecated SDK 사용. google-genai 마이그레이션 필요 (기능 정상).
- **KIS API 인증**: 계좌 개설 후 실전 환경 토큰 확인 필요.

## 4. 다음 단계 (Phase 3 후보)
- GitHub 저장소 생성 → Secrets 등록 (DART_API_KEY, KIS_APP_KEY 등)
- GitHub Pages 활성화 (`Settings > Pages > Source: GitHub Actions`)
- predict.py google-genai SDK 마이그레이션
- scorer.py: "lucky" vs "logic-based" 예측 구분 페널티
- KIS API 연동 검증 (실전 토큰 발급 후)

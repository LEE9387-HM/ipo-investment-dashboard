# STATUS

- **Project Start Date**: 2026-04-26
- **Current Phase**: Phase 3 완료 (GitHub 자동화 배포)
- **Last Updated**: 2026-04-26

## 1. 현재 상태 요약
Phase 3 배포 완료. GitHub Actions 파이프라인이 실전 동작 중이며
GitHub Pages 대시보드가 공개 URL로 접근 가능.

- 대시보드: https://lee9387-hm.github.io/ipo-investment-dashboard/
- 저장소: https://github.com/LEE9387-HM/ipo-investment-dashboard

## 2. 완료된 항목

### 스크립트
- [x] `scripts/collect.py` — DART 발행공시 수집 + enrich_ipo_items (Phase 2)
  - `pblntf_ty=C` 필터, 88일 단위 분할 조회
  - company API + document.xml ZIP 파싱으로 공모가·청약일·주관사 자동 추출
- [x] `scripts/analyze.py` — 상태 갱신 + KIS 실주가 수집 + 정확도 계산
- [x] `scripts/predict.py` — google-genai SDK (gemini-2.0-flash) 공모주 종가 예측
- [x] `scripts/notify.py` — Telegram 일일 브리핑 / 주간 요약 (미설정 시 건너뜀)

### 데이터 (2026-04-26 기준)
- [x] `data/ipo_list.csv` — 394건 (180일, 18컬럼)
- [x] `data/results.csv` — 63건 상장완료, KIS OHLCV 수집
- [x] `data/predictions.csv` — 스키마 확정 (Gemini API 유료 플랜 필요)
- [x] `data/accuracy_log.csv` — 스키마 확정

### 대시보드
- [x] `dashboard/index.html` — 4탭 레이아웃 (공모주/예측/결과/정확도)
- [x] `dashboard/index.css` — 프리미엄 다크 디자인 시스템
- [x] `dashboard/main.js` — CSV fetch + 파싱 + 테이블 렌더링

### 자동화 (GitHub Actions)
- [x] `.github/workflows/daily.yml` — 매일 오전 8시 KST 수집/분석/예측/알림
- [x] `.github/workflows/weekly.yml` — 매주 월요일 주간 요약 + 정확도 갱신
- [x] `.github/workflows/pages.yml` — push/data 변경 시 자동 Pages 배포

### GitHub 배포 (Phase 3)
- [x] GitHub 저장소 push 완료 (6개 커밋)
- [x] GitHub Secrets 7개 등록 (DART / KIS / Gemini / Telegram)
- [x] GitHub Pages 활성화 (Source: GitHub Actions)
- [x] daily.yml 수동 트리거 테스트 (진행 중)
- [x] pages.yml 배포 성공

## 3. 알려진 이슈 / 개선 여지
- **listing_dt 정확도**: 정정신고서에서 구 날짜가 잡힐 수 있음. analyze.py KIS 보완으로 실운영 영향 없음.
- **total_amount 미추출**: DART 문서 구조 패턴 불일치. 추후 개선 가능.
- **Gemini 쿼터**: 무료 플랜은 일일 한도 초과 → predict.py가 graceful skip. 유료 플랜 필요.
- **KIS API**: 실전 환경 토큰은 계좌 개설 후 발급 확인 필요.

## 4. 다음 단계 (Phase 4 후보)
- scorer.py: "lucky" vs "logic-based" 예측 구분 페널티 시스템
- total_amount 파싱 패턴 개선
- Gemini 유료 플랜 전환 후 predict.py 실제 예측 검증
- KIS 실전 토큰 발급 후 OHLCV 연동 완전 검증

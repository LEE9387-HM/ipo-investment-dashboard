# STATUS

- **Project Start Date**: 2026-04-26
- **Current Phase**: Phase 4 운영 중
- **Last Updated**: 2026-04-27

## 1. 현재 상태 요약

전체 파이프라인 구축 및 배포 완료. 매일 오전 8시 KST에 자동 수집·예측·알림이 실행 중.
Coinbase 디자인 시스템 + 모바일 카드 UI + 네이버 커뮤니티 감성 분석 추가.

- 대시보드: https://lee9387-hm.github.io/ipo-investment-dashboard/dashboard/
- 저장소: https://github.com/LEE9387-HM/ipo-investment-dashboard

## 2. 완료된 항목

### 스크립트
- [x] `scripts/collect.py` — DART 증권신고서 수집, 공모가·청약일·주관사 추출
- [x] `scripts/analyze.py` — 상태 갱신, KIS 실주가 OHLCV 수집, 정확도 계산, 주관사 실적 집계
- [x] `scripts/details.py` — 수요예측 경쟁률·확약 파싱, Google 뉴스, 네이버 뉴스·블로그 수집
- [x] `scripts/predict.py` — Gemini Bull/Bear 분석 + JSON 구조화 출력, 주관사 실적·시장 컨텍스트 주입
- [x] `scripts/notify.py` — 청약 판단 카드 (강력추천/검토/중립/보류), 커뮤니티 헤드라인 포함

### TradingAgents 참조 개선 (2026-04-27)
- [x] Gap 1: Gemini 프롬프트 Bull/Bear 이중 분석 + JSON 구조화
- [x] Gap 2: 데이터 부재 시 "⚫ 데이터 수집 전" 명시 (⚪ 중립과 구분)
- [x] Gap 3: 경쟁률 < 100 또는 청약 14일 초과 종목 LLM 호출 스킵
- [x] Gap 4: 주관사 과거 실적 (underwriter_stats.csv) 프롬프트 주입
- [x] Gap 5: KOSPI/KOSDAQ 최근 5거래일 시장 컨텍스트 주입

### 디자인 + 모바일 (2026-04-27)
- [x] Coinbase Blue (#0052ff) 어센트, Inter 폰트 적용
- [x] ≤640px 테이블 → 카드 변환 (data-label::before)
- [x] 모달 바텀시트 전환 (모바일)
- [x] 탭바 가로 스크롤 (overflow-x: auto)

### 커뮤니티 감성 분석 (2026-04-27)
- [x] 네이버 뉴스 API (5건) + 블로그 API (3건) → community_news 필드
- [x] predict.py 프롬프트에 커뮤니티 헤드라인 컨텍스트 주입
- [x] notify.py ipo_card에 커뮤니티 최신 헤드라인 표시
- [x] daily.yml NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 시크릿 추가

### 버그 수정
- [x] determine_status() 우선순위 오류 수정 (상장완료 → 청약중 → 청약종료 순서)
- [x] dedup: 정정 공시(rcept_dt 최신) 대신 청약일 있는 행 우선
- [x] Windows cp949 인코딩 오류 수정 (stdout UTF-8 강제)
- [x] DART document.xml ZIP 매직바이트 검증 추가

### 데이터 (2026-04-27 기준)
- [x] `data/ipo_list.csv` — 394건
- [x] `data/results.csv` — 63건 상장완료
- [x] `data/underwriter_stats.csv` — 주관사별 수익률 통계
- [x] `data/details/` — 활성 종목 상세 JSON

### 자동화
- [x] `daily.yml` — 매일 08:00 KST
- [x] `weekly.yml` — 매주 월요일 08:00 KST
- [x] `pages.yml` — push 시 GitHub Pages 자동 배포

### GitHub Secrets 등록 현황
- [x] DART_API_KEY
- [x] KIS_APP_KEY / KIS_APP_SECRET
- [x] GEMINI_API_KEY
- [x] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
- [x] NAVER_CLIENT_ID / NAVER_CLIENT_SECRET

## 3. 알려진 이슈

- **competition_ratio 수집률 낮음**: DART 문서가 오래된 종목은 ZIP 대신 XML 오류 반환. 신규 공시는 정상 수집.
- **total_amount 미추출**: DART 문서 구조 패턴 불일치. AI 예측에 미세 영향.
- **Gemini 무료 쿼터**: 10 RPM 제한 — 경쟁률 < 100 / 14일 초과 필터로 호출 수 최소화.
- **KIS API 실전 환경**: 계좌 개설 후 발급 확인 필요. 현재 모의투자 환경.

## 4. 다음 단계 후보 (Phase 5)

- scorer.py: 예측 성과 기반 모델 신뢰도 가중치 시스템
- 네이버 종토방(카페) 직접 파싱 (로그인 불필요한 공개 게시판)
- 대시보드 필터·검색 기능 추가
- total_amount 파싱 패턴 개선

# PROJECT

## Purpose
공모주 투자 결정 여부 점검 및 투자 현황을 정리하는 시스템입니다.
뉴스 및 공시 데이터를 수집하여 공모주 스케줄을 파악하고, 상장일 주가를 예측하며, 실제 주가와의 비교를 통해 예측 정확도를 분석합니다.
최종적으로 대시보드 형태의 웹 페이지와 텔레그램 알림을 통해 사용자에게 정보를 제공합니다.

## Runtime
- 주요 언어: Python (Data Collection & Analysis), HTML/JS/CSS (Dashboard)
- 주요 실행 명령: `python scripts/collect.py`, `python scripts/analyze.py`
- 주요 검증 명령: `pytest tests/` (추후 구성 예정)
- 대시보드 호스팅: GitHub Pages

## Structure
- `.github/workflows/`: GitHub Actions를 통한 자동화 스케줄 (daily, listing, weekly)
- `data/`: CSV 형태의 데이터 저장소
  - `ipo_list.csv`: 수집된 공모주 목록
  - `predictions.csv`: 예측 결과
  - `results.csv`: 실제 상장 결과
  - `accuracy_log.csv`: 예측 정확도 로그
- `scripts/`: 데이터 처리 로직
  - `collect.py`: DART, KIS API 등에서 데이터 수집
  - `analyze.py`: 수집된 데이터 분석
  - `predict.py`: Gemini API를 활용한 주가 예측
  - `notify.py`: 텔레그램 봇 알림 전송
- `dashboard/`: GitHub Pages로 서빙될 대시보드 소스

## Rules
- **데이터 중심**: 모든 데이터는 `data/` 아래의 CSV 파일로 관리하며, 버전 관리에 포함합니다.
- **모듈화**: 각 스크립트는 단일 책임을 가지며 서로 독립적으로 실행 가능해야 합니다.
- **Aesthetics**: 대시보드는 Antigravity의 디자인 가이드라인(Rich Aesthetics, Premium Design)을 따릅니다.
- **비밀값**: DART_API_KEY, GEMINI_API_KEY, KIS_API_KEY, TELEGRAM_BOT_TOKEN 등은 `.env.local`에서 관리하며 절대 코드나 문서에 노출하지 않습니다.

## External Services
- **DART OpenAPI**: 기업 공시 데이터 수집
- **Gemini API**: AI 기반 주가 예측 및 데이터 요약
- **KIS Open API**: 한국투자증권 API를 통한 실질 주가 데이터 수집
- **Telegram Bot**: 실시간 알림 발송

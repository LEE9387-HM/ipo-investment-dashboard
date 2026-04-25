# HANDOFF

- updated_at: 2026-04-26
- from_agent: claude-leader (Leader)
- to_agent: claude-leader (Leader)
- role: phase2-detail-parsing
- status: phase1-complete

## Objective
Phase 1 전체 골격 구현 완료. Phase 2 목표:
DART 개별 공시 XML에서 청약일, 공모가 밴드, 주관사를 자동 파싱하여
`data/ipo_list.csv`의 빈 필드를 채운다.

## What Was Done (Phase 1)
| 파일 | 내용 |
|---|---|
| `scripts/collect.py` | DART list API → ipo_list.csv upsert |
| `scripts/analyze.py` | 상태 갱신, KIS OHLCV 수집, 정확도 계산 |
| `scripts/predict.py` | Gemini API 예측 → predictions.csv |
| `scripts/notify.py`  | Telegram 일일/주간 알림 |
| `dashboard/index.html/css/js` | 4탭 다크 대시보드 |
| `.github/workflows/*.yml` | daily / weekly / pages 자동화 |

## Must Read Before Phase 2
- `scripts/collect.py`: `parse_disclosure_list()` 함수 — Phase 2 상세 파싱 여기에 추가
- `STATUS.md`: 현재 빈 필드 목록 확인

## Next Steps (Phase 2)
1. **DART 문서 API** (`/api/document.xml`) 호출하여 개별 공시 원문 수신
2. `corp_code`로 DART 회사 API에서 `market` 필드 보완
3. XML/HTML 파싱으로 다음 필드 추출:
   - `subscription_start_dt`, `subscription_end_dt`
   - `listing_dt`
   - `offering_price_low`, `offering_price_high`, `offering_price_final`
   - `total_shares`, `total_amount`
   - `underwriter`
4. `collect.py`의 `run()` 함수 마지막에 상세 파싱 호출 추가
5. `status` 필드 로직은 `analyze.py`가 담당 — collect에서는 건드리지 않음

## User Actions Required First
- `.env.local` 파일 생성 (`.env.local.example` 참조)
- `DART_API_KEY` 발급 및 등록
- `pip install -r requirements.txt`
- `python scripts/collect.py` 실행 → `data/ipo_list.csv` 확인

## Verification Checklist
- [ ] `python scripts/collect.py` 실행 후 ipo_list.csv에 행 존재
- [ ] `python scripts/analyze.py` 실행 후 status 컬럼 갱신됨
- [ ] `dashboard/index.html` 로컬 브라우저 열기 → 테이블 렌더링 확인

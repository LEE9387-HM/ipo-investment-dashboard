# HANDOFF

- updated_at: 2026-04-26
- from_agent: claude-leader (Leader)
- to_agent: claude-leader (Leader)
- role: phase3-github-deploy
- status: phase2-complete

## Objective
Phase 2 완료. collect.py가 DART API로 실제 공모주 데이터를 수집하고
상세 필드(청약일, 공모가, 주관사)를 document XML 파싱으로 자동 채운다.
Phase 3 목표: GitHub Actions 자동화 파이프라인 실전 배포.

## What Was Done (Phase 2)

### collect.py 개선
| 변경 | 내용 |
|---|---|
| `pblntf_ty=C` 복원 | DART 발행공시 필터 — 45,000건→2,200건 압축 |
| 88일 분할 조회 | `collect_period()` 추가, DART 90일 제한 대응 |
| `enrich_ipo_items()` | market + 상세 필드 자동 보완 (Phase 2 핵심) |
| `fetch_company_info()` | DART company API → KOSPI/KOSDAQ/KONEX 분류 |
| `fetch_document_text()` | document.xml ZIP 다운로드·해제·텍스트 반환 |
| `extract_offering_details()` | 청약기일, 희망공모가, 확정공모가, 주관사 정규식 추출 |

### 파싱 현황 (실 테스트 기준)
| 필드 | 추출 여부 | 비고 |
|---|---|---|
| market | ✓ | company API corp_cls 기반 |
| subscription_start_dt | ✓ | 청약기일 패턴 |
| subscription_end_dt | ✓ | 청약기일 패턴 |
| offering_price_low | ✓ | 희망공모가 밴드 하단 |
| offering_price_high | ✓ | 희망공모가 밴드 상단 |
| offering_price_final | △ | 확정 후 문서에서만 추출 |
| total_shares | ✓ | 공모주식수 |
| total_amount | △ | 패턴 개선 여지 |
| underwriter | ✓ | 대표주관회사 |
| listing_dt | △ | 정정 문서 기준으로 정확도 이슈, analyze.py KIS로 보완 |

## Must Read Before Phase 3
- `STATUS.md`: 알려진 이슈 목록 확인
- `scripts/collect.py`: `enrich_ipo_items()` → `extract_offering_details()` 구조
- `.env.local`: 4개 API 키 등록 완료

## Next Steps (Phase 3)

### 필수 (GitHub 배포)
1. **GitHub 저장소 생성** — public 또는 private
2. **Secrets 등록**:
   - `DART_API_KEY`
   - `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ENVIRONMENT`
   - `GEMINI_API_KEY`
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
3. **GitHub Pages 활성화** (`Settings > Pages > Source: GitHub Actions`)
4. `.github/workflows/*.yml` Actions 수동 트리거 테스트

### 개선 (선택)
5. `predict.py` → `google-genai` SDK 마이그레이션 (deprecated 경고 해소)
6. `extract_offering_details()` → `total_amount` 패턴 보완
7. `listing_dt` → 정정 문서에서 최신 값 추출 정확도 개선

## Verification Checklist
- [x] `python scripts/collect.py` 실행 → ipo_list.csv에 데이터 수집됨
- [ ] `python scripts/analyze.py` 실행 → status 컬럼 갱신 확인
- [ ] `python scripts/predict.py` 실행 → predictions.csv 생성 확인
- [ ] GitHub Actions daily.yml 수동 트리거 → 정상 완료 확인
- [ ] GitHub Pages 배포 후 대시보드 브라우저 확인
